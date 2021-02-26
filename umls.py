import json
from collections import Counter
import spacy
import scispacy
from scispacy.umls_linking import UmlsEntityLinker
import os
class UMLS_KB(object):
    
    def __init__(self, umls_version= None):
        
        if umls_version is None:
#             if os.path.exists("nlp_model"):
#                 print("loading nlp model from path")
#                 from spacy.language import Language
#                 Language.factories['EntityLinker'] = lambda nlp, **cfg: UmlsEntityLinker( **cfg)
#                 self.nlp = spacy.load('nlp_model')
                
#             else:
            print("creating nlp model")
            self.linker = UmlsEntityLinker(resolve_abbreviations=True)
            self.nlp = spacy.load("en_core_sci_sm")
            self.nlp.add_pipe(self.linker)
            self.umls_data = self.linker.kb.cui_to_entity
#             self.nlp.to_disk('nlp_model')
        else:
            self.umls_data = None
            self.load(umls_version)
            
        self.umls_version = umls_version


    def load(self, umls_version):
        json_path = 'data/UMLS/%s.json' % umls_version
        with open(json_path, 'r') as json_f:
            self.umls_data = json.load(json_f)

    def get_sts(self, cui):
        if self.umls_version is None:
            return self.linker.kb.cui_to_entity[cui][3]
        return self.umls_data[cui]['STY']

    def get_aliases(self, cui, include_name=True):
        if self.umls_version is None:
            aliases = self.linker.kb.cui_to_entity[cui][2]
            return aliases
        aliases = self.umls_data[cui]['STR']
        if include_name:
            aliases.append(self.umls_data[cui]['Name'])

        return aliases

    def get_all_cuis(self):
        # 
        return list(self.umls_data.keys())
#         return list(self.umls_data.keys())

    def get_all_stys(self):
        #   
        print('get_all_stys')
        all_stys = set()

        for v in self.umls_data.values():
            all_stys.add(v[3])
            
#         for cui in self.get_all_cuis():
#             print("cui = ")
#             print(cui)
#             for sty in self.get_sts(cui):
#                 all_stys.add(sty)
        return list(all_stys)

    def get_sty_sizes(self):
        # 
        sty_sizes = Counter()
        for cui in self.get_all_cuis():
            for sty in self.get_sts(cui):
                sty_sizes[sty] += 1
        return list(sty_sizes.most_common())
    def get_name(self, cui):
        
        return self.linker.kb.cui_to_entity[cui][1]
        
    def pprint(self, cui):
#         cui_info = self.umls_data[cui]
        s = ''
        s += 'CUI: %s Name: %s\n' % (cui, self.get_name(cui))
        # s += 'Definition: %s\n' % '; '.join(cui_info['DEF']) 
        s += 'Aliases (%d): %s\n' % (len(self.get_aliases(cui)), '; '.join(self.get_aliases(cui)[:5]))
        s += 'Types: %s\n' % '; '.join(self.get_sts(cui))
        print(s)
        
        
#         cui_info = self.umls_data[cui]
#         s = ''
#         s += 'CUI: %s Name: %s\n' % (cui, cui_info['Name'])
#         # s += 'Definition: %s\n' % '; '.join(cui_info['DEF']) 
#         s += 'Aliases (%d): %s\n' % (len(cui_info['STR']), '; '.join(cui_info['STR'][:5]))
#         s += 'Types: %s\n' % '; '.join(cui_info['STY'])
#         print(s)

umls_kb_st21pv = UMLS_KB()
umls_kb_full = UMLS_KB()
# umls_kb_st21pv = UMLS_KB('umls.2017AA.active.st21pv')
# umls_kb_full = UMLS_KB('umls.2017AA.active.full')


if __name__ == '__main__':
    umls_kb_st21pv.pprint('C0001097')
