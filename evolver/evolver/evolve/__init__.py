"""Evolutionary strategy search with an overfitting-resistant fitness.

LLM-as-mutation-operator (FunSearch/OPRO/EvoPrompt) + Quality-Diversity (MAP-Elites)
+ Deflated Sharpe / CSCV-PBO + recency/execution gating. The methods are research-proven
but under-used in crypto RV; the gate is what stops the search from manufacturing the
fantasy edges we spent the night killing.
"""
