# SFX natifs — contrat et sondes

Contrat : `docs/M5_DEMO_GBS2.md`, `docs/FORMAT_PROJET.md`.
Générateur : `ngpcraft/project_sfx.py`. Fixture de projet reproductible :
`tests/test_sfx_events.py` dérive Sample Town/Cave du modèle publié, ajoute
un crash explicite, exporte sans BGM puis compile avec Toshiba. Cas distinct
Save/Load : annulation du SFX transitoire. Le contrôle d'absence utilise le
projet sans SFX et les audits publics du linker.

La sonde Clang/Wasm exécute le C natif sélectionné : priorités, remplacement,
expiration, service doublé dans un VBlank et compteur de paquets perdus nul.
La sonde externe ROM du paquet complet compte 15 appels réels dans Space
Battle, constate le passage vers Path, conserve hash/core/timing et captures.
Aucun instrumentation du jeu. Commandes et preuves dans le paquet livré.
