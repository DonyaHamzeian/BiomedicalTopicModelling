
# Biomedical Topic Modelling
This repo contains the codes for generating the results of the Master's thesis with this title: Using Machine Learning Algorithms for Finding the Topics of COVID-19 Open Research Dataset Automatically. You can read the full thesis here https://uwspace.uwaterloo.ca/handle/10012/16834


# External Repositories
Two main repositories that were used include "MedLinker" (https://github.com/danlou/MedLinker) and "Contextualized Topic Models (CTM)" (https://github.com/MilaNLProc/contextualized-topic-models). 

# MedLinker

ECIR 2020 - MedLinker: Medical Entity Linking with Neural Representations and Dictionary Matching

Link to paper:
https://link.springer.com/chapter/10.1007/978-3-030-45442-5_29

We used MedLinker in the preprocessing submodule applied to the CORD-19 asbtract documents by recognizing the biomedical entities and linking them to the UMLS concepts. These two tasks were done using BERT. In the original repo, UMLS Knowledge Base was created on the local machine. However, since building the Knowledge Base on the remote server was challenging, we changed the code to use the Scispacy package and managed to achieve the similar results.  

# Contextualized Topic Model 

We used this repo for topic modelling. This is the PyTorch implementation of the LDA and ProdLDA using Variational Autoencoders based on this paper: 

ICLR 2017 - Autoencoding Variational Inference For Topic Models

Link to paper:
https://arxiv.org/abs/1703.01488

The original code incorporate BERT in the network. However, based on our experiments BERT did not improve the coherence measures. Therefore, we changed the code to remove BERT from the LDA/ProdLDA submodule. 


### To produce the results of the thesis, please refer to the CORD19TM.ipynb jupyter notebook.
