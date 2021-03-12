import os
from collections import defaultdict
import multiprocessing as mp

import numpy as np
import datetime
import warnings
import torch
from torch import optim
from torch.utils.data import DataLoader
from torch.optim.lr_scheduler import ReduceLROnPlateau
from contextualized_topic_models.networks.decoding_network import DecoderNetwork
from contextualized_topic_models.networks.decoding_network_NoBERT import DecoderNetworkNoBERT
import random
import wordcloud
import matplotlib.pyplot as plt
from scipy.special import softmax
from contextualized_topic_models.evaluation import measures


class CTM(object):
    """Class to train the contextualized topic model. This is the more general class that we are keeping to
    avoid braking code, user should use the two subclasses ZeroShotTM and CombinedTm to do topic modeling.

        :param input_size: int, dimension of input
        :param bert_input_size: int, dimension of input that comes from BERT embeddings
        :param inference_type: string, you can choose between the contextual model and the combined model
        :param n_components: int, number of topic components, (default 10)
        :param model_type: string, 'prodLDA' or 'LDA' (default 'prodLDA')
        :param hidden_sizes: tuple, length = n_layers, (default (100, 100))
        :param activation: string, 'softplus', 'relu', (default 'softplus')
        :param dropout: float, dropout to use (default 0.2)
        :param learn_priors: bool, make priors a learnable parameter (default True)
        :param batch_size: int, size of batch to use for training (default 64)
        :param lr: float, learning rate to use for training (default 2e-3)
        :param momentum: float, momentum to use for training (default 0.99)
        :param solver: string, optimizer 'adam' or 'sgd' (default 'adam')
        :param num_epochs: int, number of epochs to train for, (default 100)
        :param reduce_on_plateau: bool, reduce learning rate by 10x on plateau of 10 epochs (default False)
        :param num_data_loader_workers: int, number of data loader workers (default cpu_count). set it to 0 if you are using Windows
    """

    def __init__(self, input_size, bert_input_size, inference_type, n_components=10, model_type='prodLDA',
                 hidden_sizes=(100, 100), activation='softplus', dropout=0.2,
                 learn_priors=True, batch_size=64, lr=2e-3, momentum=0.99,
                 solver='adam', num_epochs=100, reduce_on_plateau=False, num_data_loader_workers=mp.cpu_count(),texts = None ):
        warnings.simplefilter('always', DeprecationWarning)

        if self.__class__.__name__ == "CTM":

            warnings.warn("Direct call to CTM is deprecated and will be removed in version 2, use CombinedTM or ZeroShotTM", DeprecationWarning)
        # seed everything
        seed = 10
        torch.manual_seed(seed)
        torch.cuda.manual_seed_all(seed)
        torch.cuda.manual_seed(seed)
        np.random.seed(seed)
        random.seed(seed)
        torch.backends.cudnn.deterministic = True
        torch.backends.cudnn.benchmark = False
        assert isinstance(input_size, int) and input_size > 0,\
            "input_size must by type int > 0."
        assert isinstance(n_components, int) and input_size > 0,\
            "n_components must by type int > 0."
        assert model_type in ['LDA', 'prodLDA'],\
            "model must be 'LDA' or 'prodLDA'."
        assert isinstance(hidden_sizes, tuple), \
            "hidden_sizes must be type tuple."
        assert activation in ['softplus', 'relu'], \
            "activation must be 'softplus' or 'relu'."
        assert dropout >= 0, "dropout must be >= 0."
        assert isinstance(learn_priors, bool), "learn_priors must be boolean."
        assert isinstance(batch_size, int) and batch_size > 0,\
            "batch_size must be int > 0."
        assert lr > 0, "lr must be > 0."
        assert isinstance(momentum, float) and 0 < momentum <= 1,\
            "momentum must be 0 < float <= 1."
        assert solver in ['adam', 'sgd'], "solver must be 'adam' or 'sgd'."
        assert isinstance(reduce_on_plateau, bool),\
            "reduce_on_plateau must be type bool."
        assert isinstance(num_data_loader_workers, int) and num_data_loader_workers >= 0, \
            "num_data_loader_workers must by type int >= 0. set 0 if you are using windows"

        self.input_size = input_size
        self.n_components = n_components
        self.model_type = model_type
        self.hidden_sizes = hidden_sizes
        self.activation = activation
        self.dropout = dropout
        self.learn_priors = learn_priors
        self.batch_size = batch_size
        self.lr = lr
        self.bert_size = bert_input_size
        self.inference_type = inference_type
        self.momentum = momentum
        self.solver = solver
        self.num_epochs = num_epochs
        self.reduce_on_plateau = reduce_on_plateau
        self.num_data_loader_workers = num_data_loader_workers
        self.scores_train = []
        self.best_epoch = -1
        if self.inference_type =="noBERT":
            self.model = DecoderNetworkNoBERT(
                input_size, self.bert_size, inference_type, n_components, model_type, hidden_sizes, activation,
                dropout, learn_priors)
        else:
            self.model = DecoderNetwork(
                input_size, self.bert_size, inference_type, n_components, model_type, hidden_sizes, activation,
                dropout, learn_priors)
        # init optimizer
        if self.solver == 'adam':
            self.optimizer = optim.Adam(
                self.model.parameters(), lr=lr, betas=(self.momentum, 0.99))
        elif self.solver == 'sgd':
            self.optimizer = optim.SGD(
                self.model.parameters(), lr=lr, momentum=self.momentum)

        # init lr scheduler
        if self.reduce_on_plateau:
            self.scheduler = ReduceLROnPlateau(self.optimizer, patience=10)

        # performance attributes
        self.best_cv_score = -1

        # training atributes
        self.model_dir = None
        self.train_data = None
        self.nn_epoch = None

        # learned topics
        self.best_components = None

        # Use cuda if available
        if torch.cuda.is_available():
            self.USE_CUDA = True
        else:
            self.USE_CUDA = False

        if self.USE_CUDA:
            self.model = self.model.cuda()
        self.texts = texts
    def _loss(self, inputs, word_dists, prior_mean, prior_variance,
              posterior_mean, posterior_variance, posterior_log_variance):

        # KL term
        # var division term
        var_division = torch.sum(posterior_variance / prior_variance, dim=1)
        # diff means term
        diff_means = prior_mean - posterior_mean
        diff_term = torch.sum(
            (diff_means * diff_means) / prior_variance, dim=1)
        # logvar det division term
        logvar_det_division = \
            prior_variance.log().sum() - posterior_log_variance.sum(dim=1)
        # combine terms
        KL = 0.5 * (
            var_division + diff_term - self.n_components + logvar_det_division)

        # Reconstruction term
        RL = -torch.sum(inputs * torch.log(word_dists + 1e-10), dim=1)

        loss = KL + RL

        return loss.sum()

    def _train_epoch(self, loader):
        """Train epoch."""
        self.model.train()
        train_loss = 0
        samples_processed = 0
        if self.inference_type == "noBERT":
            for batch_samples in loader:
                # batch_size x vocab_size
                X = batch_samples['X']
                X = X.reshape(X.shape[0], -1)
                if self.USE_CUDA:
                    X = X.cuda()

                # forward pass
                self.model.zero_grad()
                prior_mean, prior_variance, \
                    posterior_mean, posterior_variance, posterior_log_variance, \
                    word_dists = self.model(X)

                # backward pass
                loss = self._loss(
                    X, word_dists, prior_mean, prior_variance,
                    posterior_mean, posterior_variance, posterior_log_variance)
                loss.backward()
                self.optimizer.step()

                # compute train loss
                samples_processed += X.size()[0]
                train_loss += loss.item()
                
        else: 
            for batch_samples in loader:
                # batch_size x vocab_size
                X = batch_samples['X']
                X = X.reshape(X.shape[0], -1)
                X_bert = batch_samples['X_bert']
                if self.USE_CUDA:
                    X = X.cuda()
                    X_bert = X_bert.cuda()

                # forward pass
                self.model.zero_grad()
                prior_mean, prior_variance, \
                    posterior_mean, posterior_variance, posterior_log_variance, \
                    word_dists = self.model(X, X_bert)

                # backward pass
                loss = self._loss(
                    X, word_dists, prior_mean, prior_variance,
                    posterior_mean, posterior_variance, posterior_log_variance)
                loss.backward()
                self.optimizer.step()

                # compute train loss
                samples_processed += X.size()[0]
                train_loss += loss.item()
                
                
        train_loss /= samples_processed

        return samples_processed, train_loss

    def fit(self, train_dataset,  save_dir=None, verbose=True,  save_every = 0):
        """
        Train the CTM model.

        :param train_dataset: PyTorch Dataset class for training data.
        :param save_dir: directory to save checkpoint models to.
        """
        # Print settings to output file
        if verbose:
            print("Settings: \n\
                   N Components: {}\n\
                   Topic Prior Mean: {}\n\
                   Topic Prior Variance: {}\n\
                   Model Type: {}\n\
                   Hidden Sizes: {}\n\
                   Activation: {}\n\
                   Dropout: {}\n\
                   Learn Priors: {}\n\
                   Learning Rate: {}\n\
                   Momentum: {}\n\
                   Reduce On Plateau: {}\n\
                   Save Dir: {}".format(
                       self.n_components, 0.0,
                       1. - (1./self.n_components), self.model_type,
                       self.hidden_sizes, self.activation, self.dropout, self.learn_priors,
                       self.lr, self.momentum, self.reduce_on_plateau, save_dir))

        self.model_dir = save_dir
        self.train_data = train_dataset
        train_loader = DataLoader(
            self.train_data, batch_size=self.batch_size, shuffle=True,
            num_workers=self.num_data_loader_workers)

        # init training variables
        train_loss = 0
        samples_processed = 0

        # train loop
        for epoch in range(self.num_epochs):
            self.nn_epoch = epoch
            # train epoch
            s = datetime.datetime.now()
            sp, train_loss = self._train_epoch(train_loader)
            samples_processed += sp
            e = datetime.datetime.now()

            if verbose:
                print("Epoch: [{}/{}]\tSamples: [{}/{}]\tTrain Loss: {}\tTime: {}".format(
                    epoch+1, self.num_epochs, samples_processed,
                    len(self.train_data)*self.num_epochs, train_loss, e - s))
            if (epoch+1) %save_every ==0:
                #train set
   
                        
                train_topics = self.get_topic_lists(26)
                cv = measures.CoherenceCV(topics = train_topics, texts= self.texts)
                umass= measures.CoherenceUMASS(topics = train_topics, texts= self.texts)
                uci = measures.CoherenceUCI(topics = train_topics, texts= self.texts )
                npmi = measures.CoherenceNPMI(topics = train_topics,  texts= self.texts)
                rbo = measures.InvertedRBO(topics = train_topics)
                td = measures.TopicDiversity(topics = train_topics)


                train_cv_score =cv.score()
                train_umass_score = umass.score()
                train_uci_score = uci.score()
                train_npmi_score = npmi.score()
                train_rbo_score = rbo.score()
                train_td_score = td.score()
                
                train_scores = {'epoch': epoch, 'cv' : train_cv_score, 'umass' : train_umass_score, 'uci' : train_uci_score, 
                     'npmi' : train_npmi_score, "rbo" : train_rbo_score, 'td' : train_td_score, 'train_loss': train_loss, 
                                'topics' : train_topics}
                print('train_scores')
                print(train_scores)
                self.scores_train.append(train_scores )
                if train_cv_score > self.best_cv_score:
                    self.best_cv_score = train_cv_score
                    self.best_components = self.model.beta
                    self.best_epoch = epoch
                    if save_dir is not None:
                        self.save(save_dir)
                
                
                
#                 # test set
                
#                 cv = measures.CoherenceCV(topics = train_topics, texts= self.test_texts)
#                 umass= measures.CoherenceUMASS(topics = train_topics, texts= self.test_texts)
#                 uci = measures.CoherenceUCI(topics = train_topics, texts= self.test_texts )
#                 npmi = measures.CoherenceNPMI(topics = train_topics,  texts= self.test_texts)
#                 rbo = measures.InvertedRBO(topics = train_topics)
#                 td = measures.TopicDiversity(topics = train_topics)


#                 test_cv_score =cv.score()
#                 test_umass_score = umass.score()
#                 test_uci_score = uci.score()
#                 test_npmi_score = npmi.score()
#                 test_rbo_score = rbo.score()
#                 test_td_score = td.score()
                
#                 test_scores = {'epoch': epoch, 'cv' : test_cv_score, 'umass' : test_umass_score, 'uci' : test_uci_score, 'npmi' : test_npmi_score,  'rbo'  : test_rbo_score, 'td' : test_td_score}
                      
#                 print(test_scores)
#                 self.scores_test.append(test_scores)           



                # save best


    def get_thetas(self, dataset, n_samples=20):
        """
        Get the document-topic distribution for a dataset of topics. Includes multiple sampling to reduce variation via
        the parameter n_sample.

        :param dataset: a PyTorch Dataset containing the documents
        :param n_samples: the number of sample to collect to estimate the final distribution (the more the better).
        """
        warnings.warn("Call to `get_thetas` is deprecated and will be removed in version 2, "
                      "use `get_doc_topic_distribution` instead",
                      DeprecationWarning)
        return self.get_doc_topic_distribution(dataset, n_samples=n_samples)

    def get_doc_topic_distribution(self, dataset, n_samples=20):
        """
        Get the document-topic distribution for a dataset of topics. Includes multiple sampling to reduce variation via
        the parameter n_sample.

        :param dataset: a PyTorch Dataset containing the documents
        :param n_samples: the number of sample to collect to estimate the final distribution (the more the better).
        """
        self.model.eval()

        loader = DataLoader(
            dataset, batch_size=self.batch_size, shuffle=False,
            num_workers=self.num_data_loader_workers)

        final_thetas = []
        for _ in range(n_samples):
            with torch.no_grad():
                collect_theta = []
                if self.inference_type !='noBERT':
                    for batch_samples in loader:
                        # batch_size x vocab_size
                        X = batch_samples['X']
                        X = X.reshape(X.shape[0], -1)
                        X_bert = batch_samples['X_bert']

                        if self.USE_CUDA:
                            X = X.cuda()
                            X_bert = X_bert.cuda()

                        # forward pass
                        self.model.zero_grad()
                        collect_theta.extend(self.model.get_theta(X, X_bert).cpu().numpy().tolist())
                else:
                    for batch_samples in loader:
                        # batch_size x vocab_size
                        X = batch_samples['X']
                        X = X.reshape(X.shape[0], -1)

                        if self.USE_CUDA:
                            X = X.cuda()

                        # forward pass
                        self.model.zero_grad()
                        collect_theta.extend(self.model.get_theta(X).cpu().numpy().tolist())
                    
                    
                    
                    
                    

                final_thetas.append(np.array(collect_theta))

        return np.sum(final_thetas, axis=0)/n_samples

    def get_most_likely_topic(self, doc_topic_distribution):
        """ get the most likely topic for each document

        :param doc_topic_distribution: ndarray representing the topic distribution of each document
        """
        return np.argmax(doc_topic_distribution, axis=0)

    def predict(self, dataset, k=10):
        """Predict input."""
        self.model.eval()

        loader = DataLoader(
            dataset, batch_size=self.batch_size, shuffle=False,
            num_workers=self.num_data_loader_workers)

        preds = []

        with torch.no_grad():
            if self.inference_type !='noBERT':
                for batch_samples in loader:
                    # batch_size x vocab_size
                    X = batch_samples['X']
                    X = X.reshape(X.shape[0], -1)
                    X_bert = batch_samples['X_bert']

                    if self.USE_CUDA:
                        X = X.cuda()
                        X_bert = X_bert.cuda()

                    # forward pass
                    self.model.zero_grad()
                    _, _, _, _, _, word_dists = self.model(X, X_bert)

                    _, indices = torch.sort(word_dists, dim=1)
                    preds += [indices[:, :k]]
            else:
                for batch_samples in loader:
                    # batch_size x vocab_size
                    X = batch_samples['X']
                    X = X.reshape(X.shape[0], -1)

                    if self.USE_CUDA:
                        X = X.cuda()

                    # forward pass
                    self.model.zero_grad()
                    _, _, _, _, _, word_dists = self.model(X)

                    _, indices = torch.sort(word_dists, dim=1)
                    preds += [indices[:, :k]]
            preds = torch.cat(preds, dim=0)
        return preds

    def get_topics(self, k=10):
        """
        Retrieve topic words.

        :param k: int, number of words to return per topic, default 10.
        """
        assert k <= self.input_size, "k must be <= input size."
        component_dists = self.model.beta
        topics = defaultdict(list)
        for i in range(self.n_components):
            _, idxs = torch.topk(component_dists[i], k)
            component_words = [self.train_data.idx2token[idx]
                               for idx in idxs.cpu().numpy()]
            topics[i] = component_words
        return topics

    def get_topic_lists(self, k=10):
        """
        Retrieve the lists of topic words.


        :param k: (int) number of words to return per topic, default 10.
        """
        assert k <= self.input_size, "k must be <= input size."
        # TODO: collapse this method with the one that just returns the topics
        component_dists = self.model.beta
        topics = []
        for i in range(self.n_components):
            _, idxs = torch.topk(component_dists[i], k)
            component_words = [self.train_data.idx2token[idx]
                               for idx in idxs.cpu().numpy()]
            topics.append(component_words)
        return topics

    def _format_file(self):
        model_dir = "contextualized_topic_model_nc_{}_tpm_{}_tpv_{}_hs_{}_ac_{}_do_{}_lr_{}_mo_{}_rp_{}".\
            format(self.n_components, 0.0, 1 - (1./self.n_components),
                   self.model_type, self.hidden_sizes, self.activation,
                   self.dropout, self.lr, self.momentum,
                   self.reduce_on_plateau)
        return model_dir

    def save(self, models_dir=None):
        """
        Save model. (Experimental Feature, not tested)

        :param models_dir: path to directory for saving NN models.
        """
        warnings.simplefilter('always', Warning)
        warnings.warn("This is an experimental feature that we has not been fully tested. Refer to the following issue:"
                      "https://github.com/MilaNLProc/contextualized-topic-models/issues/38",
                      Warning)

        if (self.model is not None) and (models_dir is not None):

            model_dir = self._format_file()
            if not os.path.isdir(os.path.join(models_dir, model_dir)):
                os.makedirs(os.path.join(models_dir, model_dir))

#             filename = "epoch".format(self.nn_epoch) + '.pth'
            file_name = self.model_type+' '+  str(self.n_components)+'.pth'
            fileloc = os.path.join(models_dir, model_dir, file_name)
            with open(fileloc, 'wb') as file:
                torch.save({'state_dict': self.model.state_dict(),
                            'dcue_dict': self.__dict__}, file)

    def load(self, model_dir):
        """
        Load a previously trained model. (Experimental Feature, not tested)

        :param model_dir: directory where models are saved.
        :param epoch: epoch of model to load.
        """

        warnings.simplefilter('always', Warning)
        warnings.warn("This is an experimental feature that we has not been fully tested. Refer to the following issue:"
                      "https://github.com/MilaNLProc/contextualized-topic-models/issues/38",
                      Warning)

#         epoch_file = "epoch_"+str(epoch)+".pth"
        model_file = os.path.join(model_dir, self.model_type+' '+  str(self.n_components)+'.pth')
#         model_file = os.path.join(model_dir)
        with open(model_file, 'rb') as model_dict:
            checkpoint = torch.load(model_dict)

        for (k, v) in checkpoint['dcue_dict'].items():
            setattr(self, k, v)

        self.model.load_state_dict(checkpoint['state_dict'])

    def get_topic_word_matrix(self):
        """
        Return the topic-word matrix (dimensions: number of topics x length of the vocabulary).
        If model_type is LDA, the matrix is normalized; otherwise the matrix is unnormalized.
        """
        return self.model.topic_word_matrix.cpu().detach().numpy()

    def get_topic_word_distribution(self):
        """
        Return the topic-word distribution (dimensions: number of topics x length of the vocabulary).
        """
        mat = self.get_topic_word_matrix()
        return softmax(mat, axis=1)

    def get_word_distribution_by_topic_id(self, topic):
        """
        Return the word probability distribution of a topic sorted by probability.

        :param topic: id of the topic (int)

        :returns list of tuples (word, probability) sorted by the probability in descending order
        """
        if topic >= self.n_components:
            raise Exception('Topic id must be lower than the number of topics')
        else:
            wd = self.get_topic_word_distribution()
            t = [(word, wd[topic][idx]) for idx, word in self.train_data.idx2token.items()]
            t = sorted(t, key=lambda x: -x[1])
        return t

    def get_wordcloud(self, topic_id, n_words=5, background_color="black"):
        """
        Plotting the wordcloud. It is an adapted version of the code found here:
        http://amueller.github.io/word_cloud/auto_examples/simple.html#sphx-glr-auto-examples-simple-py and
        here https://github.com/ddangelov/Top2Vec/blob/master/top2vec/Top2Vec.py

        :param topic_id: id of the topic
        :param n_words: number of words to show in word cloud
        :param background_color: color of the background
        """
        word_score_list = self.get_word_distribution_by_topic_id(topic_id)[:n_words]
        word_score_dict = {tup[0]: tup[1] for tup in word_score_list}
        plt.figure(figsize=(10, 4), dpi=200)
        plt.axis("off")
        plt.imshow(wordcloud.WordCloud(width=1000, height=400, background_color=background_color
                                       ).generate_from_frequencies(word_score_dict))
        plt.title("Displaying Topic " + str(topic_id), loc='center', fontsize=24)
        plt.show()

    def get_predicted_topics(self, dataset, n_samples):
        """
        Return the a list containing the predicted topic for each document (length: number of documents).

        :param dataset: CTMDataset to infer topics
        :param n_samples: number of sampling of theta
        :return: the predicted topics
        """
        predicted_topics = []
        thetas = self.get_doc_topic_distribution(dataset, n_samples)

        for idd in range(len(dataset)):
            predicted_topic = np.argmax(thetas[idd] / np.sum(thetas[idd]))
            predicted_topics.append(predicted_topic)
        return predicted_topics


class ZeroShotTM(CTM):
    """
    ZeroShotTM, as described in https://arxiv.org/pdf/2004.07737v1.pdf
    """

    def __init__(self, **kwargs):
        inference_type = "zeroshot"
        super().__init__(inference_type=inference_type, **kwargs)


class CombinedTM(CTM):
    """
    CombinedTM, as described in https://arxiv.org/pdf/2004.03974.pdf
    """
    def __init__(self, **kwargs):
        inference_type = "combined"
        super().__init__(inference_type=inference_type, **kwargs)


