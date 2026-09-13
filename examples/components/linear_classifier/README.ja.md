# linear_classifier

softmax 回帰。`k`（クラス数）は YAML に書かず、trainer が dataset から読んで
`.build(k=...)` で渡す。**「YAML にそう書いてある」と「実際にそうである」がズレない**
ようにするため。
