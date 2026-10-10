# Toolchain Toshiba fournie par l'utilisateur

Ce dossier ne contient aucun binaire propriétaire et ne doit jamais en distribuer.

Placer ici la toolchain en conservant `BIN/`, ou sélectionner son emplacement avec
`--toolchain C:/chemin/toshiba`. La variable d'environnement locale
`NGPC_TOSHIBA_HOME` est également acceptée. Aucun téléchargement automatique.

Les noms de fichiers attendus sont vérifiés par `python -m ngpcraft check-toolchain`.
Une installation portable peut utiliser ce dossier ; une application installée
devra proposer un dossier utilisateur accessible en écriture. Aucun chemin local
de toolchain n'est enregistré dans les projets partageables.
