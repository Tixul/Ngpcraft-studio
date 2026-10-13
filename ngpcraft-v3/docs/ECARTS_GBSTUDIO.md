# NGPCraft et GB Studio : ce qui change, pourquoi, et comment adapter un tuto

> Pour qui : toute personne qui apprend NGPCraft **en suivant des tutoriels GB
> Studio**. Version 0.2, 2026-09-29 (décisions NgpCraft sur §1, §2, §5,
> §6, §11). Référence : GB Studio 4.3.2 publié.
>
> Le principe : NGPCraft reprend GB Studio **concept pour concept** (scènes,
> acteurs, triggers, scripts, variables, mêmes noms d'événements, mêmes
> réglages, mêmes comportements par défaut). Il ne s'en écarte que lorsque la
> **Neo Geo Pocket Color** l'impose ou lorsqu'elle permet **plus**. Chaque
> écart est listé ici, avec quatre rubriques :
>
> - **Dans GB Studio** : ce que dit le tuto ;
> - **Dans NGPCraft** : ce qui se passe ici ;
> - **Pourquoi** : la raison matérielle ;
> - **Adapter le tuto** : quoi faire concrètement.
>
> Un écart qui n'est pas dans ce document est un **bug**, à signaler. Le
> contrôle de parité automatique (cahier §3.4) interdit les écarts non déclarés.
>
> Statuts : ✅ décidé · 🟡 proposé, à confirmer · ⏳ à trancher.

---

## Ce qui est identique (inutile d'adapter)

- Les **scènes**, leurs **types** (Top-Down, Platformer, Adventure, Shoot'em
  up, Point & Click, Logo) et leurs réglages moteur (*engine fields* : mêmes
  noms, mêmes unités, mêmes valeurs par défaut).
- Les **scripts** : mêmes événements, même ordre d'exécution, mêmes fils
  (*threads*), mêmes attentes, mêmes verrous. NGPCraft exécute le même
  bytecode que GB Studio, par la même machine virtuelle (GBVM) portée sur NGPC.
- Les **variables**, les **custom scripts** avec paramètres, les conditions et
  les boucles.
- Les **positions** : même unité (sous-pixels de 1/32 px), même origine de
  sprite (`canvasOrigin`) et mêmes boîtes de collision (`bounds`).
- Les **acteurs hors écran** sont mis en pause, comme dans GB Studio, sauf
  « Keep Running While Offscreen ».

---

## 1. L'écran fait 160×152, pas 160×144 — ✅ (aller-retour ⏳)

- **Dans GB Studio** : l'écran fait 160×144 pixels, soit 20×18 tuiles.
- **Dans NGPCraft** : on garde **les proportions de la NGPC, 160×152**, soit
  **19 rangées** de tuiles, pour tous les projets, y compris ceux importés de
  GB Studio. On ne réduit pas l'écran de la console pour imiter le Game Boy.
- **Pourquoi** : c'est la taille de l'écran de la console ; la brider irait
  contre la règle « ne jamais brider la NGPC ». (Décision NgpCraft,
  2026-09-29. Le mode « compatible 160×144 à bandes » proposé en v0.1 n'est
  plus le défaut.)
- **Adapter le tuto** : dessinez vos décors d'un écran avec **152 px de haut**
  (une rangée de tuiles de plus) et vérifiez les bornes de caméra.
- **✅ Scènes de 144 px (décision NgpCraft, 2026-09-30)** : par défaut,
  l'outil **invite l'auteur à remplacer ou adapter le décor** (152 px de haut) ;
  en attendant, la scène s'affiche **avec des bandes noires équilibrées**,
  4 px en haut et 4 px en bas. Mise en œuvre prévue : fenêtre matérielle
  (`0x8002`–`0x8005`, lignes 4 à 147), couleur hors fenêtre noire (`0x83F0`),
  décalage des sprites (`0x8021`) ; dans ces scènes le moteur garde l'écran
  GB de 160×144 (caméra, bornes, activation comme GB Studio). Le décor n'est
  pas modifié, le projet reste ouvrable dans GB Studio.
  **Appliqué le 2026-09-30** (`M2_EXPORT_SCENE.md`) : les 17 scènes de
  `gbs2` se traduisent, 14 à bandes ; l'export avertit pour chacune. Les
  bandes restent noires pendant les fondus (elles ne sont pas une palette
  de la scène).
- **✅ Shmup vertical verrouillé (décision NgpCraft, 2026-10-07)** :
  pour une vue de 152 px, offset caméra **+68 vers le haut / −52 vers le bas**,
  afin de garder la distance du joueur au bord. Le GBVM utilise +64/−48 pour
  144 px ; les scènes à bandes gardent ces valeurs. Cette adaptation est
  déclarée dans l'extraction et contrôlée sur ROM : [M5_GENRES](M5_GENRES.md).
- **Historique de l'étude (avant la décision)** : le va-et-vient avec GB Studio. Un projet GB Studio
  a des scènes de 144 px de haut (14 des 17 scènes de l'exemple officiel
  `gbs2` font exactement un écran de haut). Sur NGPC, il manque donc une
  rangée en bas de l'écran. Pistes, à comparer avant de choisir :
  - que montrer dans la rangée manquante (couleur de fond, rangée répétée,
    caméra bornée autrement) ;
  - ajouter la rangée dans `ngpcraft.json` (extension de la scène), sans
    toucher au fichier GB Studio, pour que le projet reste ouvrable dans
    GB Studio ;
  - le mode à bandes (fenêtre matérielle `0x8002-05`, couleur hors fenêtre
    `0x83F0`) seulement comme option explicite d'une scène, jamais par défaut.
  Critère : un projet qui fait l'aller-retour ne perd rien et ne change pas
  d'aspect sans que l'auteur l'ait voulu.

Caméra scriptée (2026-10-08) : `EVENT_CAMERA_MOVE_TO` garde l'addition GB Studio
de 80/72 pixels pour transformer l'origine en centre. Sur le viewport NGPC
de 152 pixels, le scroll vertical correspondant est donc décalé de -4 pixels
par rapport à une origine GB de 144 pixels. Les scènes affichées en 144 pixels
conservent l'origine GB. Déplacement, axes de suivi et clamps sont portés
depuis le GBVM publié ; un pas nul après conversion ou débordant le byte est
refusé. Contrat et preuve : [M4_CAMERA_CONTROL](M4_CAMERA_CONTROL.md).

## 2. Il n'y a ni Start ni Select : tout passe par Option — ✅ (codé ; redémarrage ⏳)

- **Dans GB Studio** : boutons A, B, **Start**, **Select** et croix.
- **Dans NGPCraft** : la NGPC a A, B, **Option** et la croix (plus Power,
  que le jeu ne lit pas).
  - **Appui court sur Option = Start** ;
  - **appui long sur Option = Select**.
  (Décision NgpCraft, 2026-09-29. Remplace la proposition v0.1
  « Select = Option + B ».)
- **Pourquoi** : la console n'a qu'un bouton pour ces deux rôles ; la durée
  de l'appui permet de garder A et B libres pour le jeu.
- **Adapter le tuto** : là où le tuto dit « Start », appuyez brièvement sur
  Option ; là où il dit « Select », gardez Option enfoncé. L'éditeur signale
  chaque script qui utilise Start ou Select.
- **✅ Décision NgpCraft du 2026-10-01** : appui long ≈ **1 seconde**
  (60 images) ; une **option de projet** permet d'inverser les rôles (court =
  Select, long = Start). Les détails ont été réglés « au mieux » au codage
  (2026-10-01), ci-dessous.
- **Comment ça marche (codé le 2026-10-01)** :
  - **durée** : un appui est long à son **60e tick** de jeu (≈ 1 s), valeur
    fixe ;
  - **appui court** (relâché avant 60 ticks) : le rôle court (Start par
    défaut) est vu **pressé et tenu pendant un seul tick, celui du
    relâchement**. On ne peut pas savoir plus tôt qu'un appui sera court :
    Start part donc au relâchement, un peu plus tard que sur Game Boy, où
    il part à l'appui ;
  - **appui long** : le rôle long (Select par défaut) est pressé au 60e tick,
    sans attendre le relâchement, puis **tenu tant qu'Option reste
    enfoncé** ; relâcher ne donne alors pas de Start ;
  - **« bouton maintenu »** (*If Input*, *Await Input*) : Select maintenu =
    Option gardé au-delà d'une seconde ; Start n'est jamais maintenu plus
    d'un tick. Un script qui attend « Start maintenu » longtemps ne
    marchera pas : utiliser « Start pressé » ;
  - **inverser les rôles** : dans `ngpcraft.json`, `"ngpc":
    {"option_long_press": "start"}` (court = Select, long = Start). Valeur
    par défaut : `"select"`. Toute autre valeur est refusée ;
  - **rien d'autre ne bouge** : A, B et la croix sont lus comme avant, y
    compris pendant qu'Option est tenu ;
  - **scripts de boutons** (*Attach Script To Button*) : un script relié à
    Start part au relâchement d'un appui court ; relié à Select, au bout
    d'une seconde d'appui (`M2_INPUT_SCRIPTS.md`).
  Code : `ngpcraft/scene_topdown.py` (`input_source`), oracle
  `tools/gbvm_topdown.Input`, preuves `tests/test_topdown.py`
  (`M2_TOPDOWN.md`, section Option).
- **⏳ Le redémarrage logiciel** de GB Studio (A + B + Start + Select tenus
  ensemble, `core.c`, contrat C4) : impossible tel quel, puisque Start et
  Select viennent du même bouton. Il n'est pas encore porté dans NGPCraft.
  Proposition pour le jour où il le sera : **A + B + appui long sur Option**
  (A + B + le rôle long), à confirmer à ce moment-là.

## 3. Les sprites sont en 8×8 ; les feuilles 8×16 sont converties — ✅

- **Dans GB Studio** : une feuille de sprite est en **8×16** (par défaut) ou
  en 8×8.
- **Dans NGPCraft** : la NGPC n'a que des sprites matériels **8×8**, au nombre
  de **64**, sans limite par ligne (pas de clignotement). Une feuille 8×16 est
  découpée en deux 8×8, ce qui **double** son coût en sprites matériels.
- **Pourquoi** : c'est le matériel.
- **Adapter le tuto** : rien d'obligatoire. Pour les nouveaux projets,
  choisissez le mode **8×8** (le défaut). Surveillez le **budget de sprites**
  affiché par scène : un acteur 16×16 coûte 4 sprites NGPC, soit au plus 16
  acteurs 16×16 à l'écran. Exemple réel : dans le projet d'exemple officiel
  `gbs2`, le grand boss « elephant » coûte **36 sprites NGPC** par image, sur
  64 (mesuré par le compilateur le 2026-09-30, `M2_SPRITES.md`). Il passe,
  mais il laisse peu de place au reste de la scène.
- **Ce qui ne change pas** : l'ancre (`canvasOrigin`), les animations, les
  retournements et l'ordre d'affichage entre acteurs sont ceux de GB Studio,
  vérifiés au pixel contre la règle de dessin de l'éditeur.

## 4. Plus de palettes, mais des couleurs un peu moins fines — ✅

- **Dans GB Studio** : en couleur, 8 palettes pour le fond et 8 pour les
  sprites, sur 32 768 couleurs.
- **Dans NGPCraft** : **16 palettes** pour chaque plan de fond et 16 pour les
  sprites, **une palette par sprite 8×8**, sur **4 096 couleurs**. Les
  projets GB « mono » reçoivent une palette de gris déclarée.
- **Pourquoi** : la NGPC code ses couleurs sur 4 bits par canal au lieu de 5.
- **Adapter le tuto** : rien. À l'import, un **rapport** liste les couleurs
  qui se sont rapprochées ou confondues ; retouchez-les si besoin. Profitez
  des 16 palettes.
- **Modes de couleur d'un décor** (2026-10-10) : en mode *Manuel*, GB Studio
  lit l'image en **4 teintes d'après son seul niveau de vert** (images
  dessinées dans les verts de la Game Boy), puis applique les palettes de la
  scène : deux couleurs de même niveau de vert (rouge et noir, par exemple)
  deviennent la même teinte, et l'éditeur le signale. *Automatique* (GB
  Studio) et **NGPC natif** prennent les **vraies couleurs du PNG** et
  calculent seuls jusqu'à 16 palettes de 3 couleurs plus une couleur de fond
  commune (la plus fréquente, ou les pixels transparents). Sur NGPC,
  *Automatique* est compilé comme NGPC natif. Les couleurs natives marchent
  sur les **deux plans** : décor principal et second décor (transparent là
  où il a sa couleur de fond).
- **Palettes de sprites** (2026-10-10) : les 16 palettes de sprites de la
  console servent d'abord aux 8 palettes de sprites de GB Studio que la scène
  utilise vraiment (acteurs, projectiles, émotes, changements de palette par
  script) ; les sprites en couleurs NGPC natives prennent toutes les autres,
  jusqu'à **16 dans une scène entièrement native**.

## 5. La « fenêtre » (overlay) de GB Studio devient un plan de texte — ✅

- **Dans GB Studio** : dialogues, menus et HUD simples s'affichent sur la
  **couche fenêtre** du Game Boy (événements *Overlay*, *Display Dialogue*…).
- **Dans NGPCraft** : la NGPC n'a pas de couche fenêtre. Le texte s'affiche
  sur **un des deux plans de défilement, SCR1 ou SCR2**, celui qui est libre
  dans la scène. **Jamais en sprites** : 64 sprites 8×8 pour toute la scène,
  une boîte de dialogue les épuiserait. (Décision NgpCraft, 2026-09-29.)
  Si les deux plans servent déjà au décor, un découpage par ligne (raster)
  prend le relais, et son coût est affiché.
- **Pourquoi** : c'est le matériel ; la NGPC a en échange deux vrais plans de
  décor.
- **Adapter le tuto** : rien pour les dialogues, choix et menus. Si vous
  utilisez les **deux plans** pour le décor, l'éditeur vous indique comment le
  texte sera affiché.
- **À noter** : texte, second décor, parallaxe et tuiles prioritaires (§11)
  veulent tous un des deux plans. L'attribution des plans d'une scène sera
  décidée à la compilation et affichée (voir « Partage des deux plans » en fin
  de document).
- **Menu latéral (2026-10-09)** : *Menu* en disposition « menu » (fenêtre
  à droite, x = 10, une colonne jusqu'à 8 choix) est identique à GB Studio
  quand le texte a un plan à lui (cas A). Si les deux plans portent du
  décor (cas B), la fenêtre doit occuper toute la largeur : le menu latéral
  est refusé à la compilation, avec la scène en cause. **Adapter le tuto** :
  utiliser la disposition « dialogue » dans une scène à deux plans de décor.
- **Avatars (2026-10-07)** : identiques à GB Studio (ordre, index, décalage
  de deux cases, avatar large non découpé). Seule différence : une tuile
  d'avatar qui emploie les **quatre** nuances en montre trois (la moins
  employée prend la plus proche, avertissement à la compilation), comme le
  cadre (`M2_TEXT.md` D4). **Adapter le tuto** : dessiner l'avatar avec au
  plus trois nuances par carré de 8×8 pour un rendu exact. Détail :
  `M4_AVATARS.md`.
- **Overlays (2026-10-07)** : *Overlay Show* et *Overlay Move To* se
  comportent comme dans GB Studio (vitesses, attente, effacement partiel).
  Fenêtre **pleine largeur** seulement (`x = 0`, sinon refus localisé).
  Jusqu'à **18 rangées** (tout l'écran) quand le texte est sur SCR1 seul ;
  au-delà de 13, la fenêtre passe par une carte en RAM recopiée à chaque
  image (≈ 9 000 cycles boîte pleine) et s'affiche une image après son
  écriture, comme le cas « plan partagé ». **Adapter le tuto** : rien pour
  un écran-titre ou un logo ; un overlay décalé à droite n'est pas encore
  pris en charge. Détail : `M4_OVERLAY.md`.

Menus (2026-10-08) : `EVENT_MENU` en disposition `dialogue` accepte de un à huit
choix, les deux groupes et leurs voisins GBVM, l'annulation et une sortie par
référence. La disposition latérale `menu` à x=10 reste explicitement refusée :
le décor et les masques de sprites supposent une fenêtre pleine largeur.
Contrat, ROM et comparaison de pixels : [M4_MENU](M4_MENU.md). Validation console
P5 encore à faire.

*Overlay Set Scanline Cutoff* et boîte de dialogue **en haut** (2026-10-09) :
la fenêtre est masquée sous la ligne de coupure par un split raster du plan
de texte, comme l'interruption de ligne de GBVM ; cas A seulement (le cas B,
où SCR1 porte aussi le décor, est refusé avec sa raison). Détail :
[M6_SCANLINE_CUTOFF](M6_SCANLINE_CUTOFF.md).

## 6. La parallaxe : même effet, par le raster — ✅

- **Dans GB Studio** : la parallaxe découpe **le même** fond en 3 bandes
  horizontales qui défilent à des vitesses différentes.
- **Dans NGPCraft** : ce découpage est reproduit par un **effet raster**
  (changer le défilement à certaines lignes de l'écran). En extension NGPC,
  vous disposez aussi d'un **second fond indépendant**. (Décision NgpCraft, 2026-09-29.)
- **Comment** : on **réutilise** ce qui existe et qui est documenté (raster et
  micro-DMA du `base_template`, route d'Over Rev, `EFFECTS.md`, `DMA.md`,
  fiches sur la chaîne raster), pas de nouveau laboratoire. Consigne : la
  façon **la plus propre et la plus économique** possible, par exemple ne
  changer le défilement qu'aux lignes où une bande commence (2 changements
  pour 3 bandes), et rien du tout dans une scène sans parallaxe. Le coût
  mesuré (temps par image, mémoire) figure au rapport de la scène.
- **Pourquoi** : le Game Boy n'a qu'un fond ; la NGPC en a deux, et sait
  changer le défilement ligne par ligne.
- **Adapter le tuto** : rien. Pour aller plus loin, l'extension « second
  plan » est documentée à part.
- **Livré le 2026-10-09** ([M6_PARALLAX](M6_PARALLAX.md)) : une interruption
  Timer0 par début de bande (2 pour 3 bandes), rien sans parallaxe ; prouvé
  au pixel en natif, tremblement de caméra compris. Refusé explicitement :
  carte défilant verticalement, décor sur deux plans, texte sur le plan du
  décor dans la même scène, second décor. Comme sur Game Boy, la coupure de
  fenêtre (*Overlay Set Scanline Cutoff*) n'agit pas dans une scène avec
  parallaxe.

## 7. Musique et effets sonores : un autre instrument — ✅ / 🟡

- **Dans GB Studio** : la musique est au format **hUGETracker (`.uge`)** et
  les effets en `.wav`, `.vgm` ou FX Hammer, pour la puce son du Game Boy
  (2 voies carrées réglables, 1 voie « wave », 1 bruit).
- **Dans NGPCraft** : la NGPC a **3 voies carrées + 1 bruit, en stéréo**, et
  un processeur dédié au son. La musique se compose dans le **NGPC Sound
  Creator** (`.ngps`). Les événements (*Play Music*, *Stop Music*, *Play
  Sound Effect*…) gardent leurs noms. Un `.uge` peut être **converti**, avec
  un **rapport de pertes** (la voie wave devient une voie carrée, les
  rapports cycliques sont perdus), puis réécouté.
  Correspondance pour *Mute Channel* : voies 1, 2, 3, 4 → carrée 1, carrée 2,
  carrée 3, bruit.
- **Pourquoi** : ce n'est pas la même puce son.
- **Adapter le tuto** : les étapes « importer une musique » passent par le
  Sound Creator, ou par la conversion `.uge` suivie d'une écoute. Les
  événements de script ne changent pas.

**Livré le 2026-10-07 :** Play/Stop Music, avec la liaison explicite
`native_music` dans le sidecar. Même morceau actif : aucune relance ;
Stop puis Play : retour à l'intro. Service aux VBlank physiques pendant
dialogues/fondus et à 30 fps ; départ en attente après le chargement.
Les morceaux non accessibles sont exclus. UGE sans conversion/liaison
reste refusé. Save/Load conserve maintenant l'identité du morceau natif
et redémarre son intro après la reprise de scène, ou restaure le silence.
Voir [M5_AUDIO_SAVE](M5_AUDIO_SAVE.md).
Contrat et preuves : [M5_AUDIO_EVENTS](M5_AUDIO_EVENTS.md).

**Livré le 2026-10-09 (émulateur) :** *Play Sound Effect*
([M5_BUILTIN_SFX](M5_BUILTIN_SFX.md)) ; *Text Set Sound Effect* : l'effet
natif rejoué à chaque lettre, *Remove* coupe l'effet en cours
([M5_TEXT_SOUND](M5_TEXT_SOUND.md)) ; *Mute Channel* : voies retenues sur le
pilote d'Over Rev, une voie démutée reprend sa note en cours au lieu
d'attendre la suivante ([M5_MUTE_CHANNEL](M5_MUTE_CHANNEL.md)) ; *Set Music
Routine* : les appels `6xy` voyagent dans le flux natif (`FE 07 xy`), depuis
une conversion UGE ou une partition native ; un `.ngps` du Sound Creator n'en
porte pas ([M5_MUSIC_ROUTINE](M5_MUSIC_ROUTINE.md)).

**2026-10-11 (nuit) :** l'éditeur convertit seul, au build, les `.uge`/`.mod`
et les sons FX Hammer/VGM qui n'ont pas de version NGPC (ou dont le fichier a
changé) : un tuto GB Studio n'a plus d'étape « convertir ». Les morceaux
gardent leur **stéréo** (routage NR51 → décalage G/D par voie, `music_pan.py`) ;
un bruitage ne coupe que la voie qu'il utilise, comme sur Game Boy. Seuls les
sons **WAV** restent refusés (pas de lecteur d'échantillons).

## 8. La sauvegarde utilise la flash de la cartouche — ✅ émulateur (2026-10-07), console ⏳

- **Dans GB Studio** : *Save Data* enregistre l'état complet du jeu (jusqu'à
  3 emplacements) dans une mémoire sauvegardée par pile.
- **Dans NGPCraft** : *Save Data*, *Load Data*, *If Data Saved*, *Clear
  Data* et *Peek Data* (2026-10-09) font la même chose, avec le même état complet (variables, scripts en cours, scène,
  acteurs, pile de scènes). Au chargement, comme sur Game Boy : fondu sortant,
  retour dans la scène sauvegardée **sans rejouer son On Init**, le script
  reprend juste après le *Save Data*, dans sa branche *On Load* (avec le fondu
  entrant automatique). L'état est rangé dans la **flash** de la cartouche,
  dans un journal sur deux blocs de 8 Ko (règles éprouvées sur Over Rev ;
  détail : `M5_SAVE.md`).
- **Différences visibles** :
  - une sauvegarde qui doit effacer un bloc **gèle le jeu environ 1 s** sur
    console. Avec un état de 8 pages ou plus par emplacement, c'est le cas de
    **chaque** sauvegarde (le bloc ne tient que 3 enregistrements) ;
  - une sauvegarde faite par une **autre version** du jeu (autre export)
    est ignorée, comme si l'emplacement était vide ;
  - la sauvegarde est livrée pour les jeux à **plusieurs scènes** (au moins
    un *Switch Scene*) ; un jeu d'une seule scène est refusé à l'export ;
  - *Clear Data* n'efface pas la flash : il ajoute au journal un
    enregistrement « emplacement effacé » (comme le RESET SAVE d'Over Rev) ;
    il peut donc lui aussi tomber sur une bascule de bloc et geler ~1 s ;
  - *Peek Data* lit la variable dans la dernière sauvegarde de cette version ;
    un indice hors de l'état sauvé répond « pas de sauvegarde » (la
    variable reçoit 0) là où GB Studio lirait au-delà ;
  - l'export refuse un jeu dont l'état ne tient pas dans la flash (3
    emplacements + 1 page de reprise ≤ 32 pages), avec les chiffres.
- **Pourquoi** : les cartouches NGPC n'ont pas de mémoire sauvegardée par
  pile ; la flash s'écrit par pages et ne s'efface que par blocs entiers.
- **Adapter le tuto** : rien à changer dans les événements. Éviter de
  sauvegarder à chaque pas (gel) : sauvegarder à un point de passage.

## 9. Les limites (acteurs, triggers, projectiles) sont celles de la NGPC — ✅

- **Dans GB Studio** : avertissements à 20 acteurs par scène, 10 à l'écran,
  30 triggers, 5 projectiles.
- **Dans NGPCraft** : ces chiffres ne s'appliquent pas tels quels. L'éditeur
  affiche un **budget NGPC** par scène (sprites matériels, tuiles, mémoire,
  temps de calcul mesuré).
- **Pourquoi** : les limites de GB Studio sont calibrées pour le Game Boy ;
  la NGPC a d'autres ressources (64 sprites sans limite par ligne, un
  processeur 16/32 bits…).
- **Adapter le tuto** : suivez le budget affiché plutôt que les chiffres cités
  par le tuto.
- **Pile de scènes** (*Push / Pop Scene State*) : 8 éléments, comme GB Studio.
  Sur Game Boy, empiler une 9e fois ou dépiler une pile vide abîme la
  mémoire sans prévenir ; ici, ces deux cas ne font **rien**
  (`M2_SCENE_STACK.md`). Un tuto qui reste dans les 8 ne voit aucune
  différence.
- **Projectiles** (*Launch Projectile*, 2026-10-06) : GB Studio garde les
  5 premiers types de projectile d'une scène et **ignore sans rien dire** les
  tirs des types suivants (un avertissement à la compilation). NGPCraft les
  garde tous (jusqu'à 255 types), avec 5 projectiles en vol à la fois comme
  GBVM. Sont refusés avec leur emplacement, au lieu d'être compilés en
  « rien » ou en `undefined` : un `directionType` inconnu, le champ que le
  mode lit (direction, angle, variable, acteur visé) absent du fichier, et
  des scripts de touche dans une scène qui n'est ni Top Down ni Platformer
  (leurs collisions ne sont pas encore livrées). Un tuto qui reste dans
  les 5 types ne voit aucune différence (`M4_PROJECTILES.md`).

## 10. Des tuiles partagées entre décor et sprites — ✅

- **Dans GB Studio** : jusqu'à 192 tuiles de fond uniques (384 en couleur),
  plus les tuiles de sprites.
- **Dans NGPCraft** : **512 tuiles au total**, partagées par le décor, les
  sprites, le texte et la police.
- **Pourquoi** : la mémoire des tuiles de la NGPC.
- **Adapter le tuto** : les décors des tutoriels tiennent largement (le plus
  chargé de l'exemple officiel `gbs2` en utilise 191). Le budget de la scène
  prévient en cas de dépassement.

## 11. Les tuiles « prioritaires » (devant les sprites) — ✅ livré (décor 2026-10-02/03, sprites 2026-10-09)

**État au 2026-10-09** : priorité du décor rendue (masque A3 sur SCR1, ou
découpe dynamique des sprites avec le complément A4 — [A3_PRIORITES](A3_PRIORITES.md)) ;
priorité des **sprites** rendue exactement, derrière les deux plans, avec un
décor compilé par indice GB ([M6_SPRITE_PRIORITY](M6_SPRITE_PRIORITY.md)).
Seul refus : une scène à sprites prioritaires avec plusieurs couleurs 0
(la NGPC n'a qu'une couleur de fond), message avec le pixel. Les
paragraphes ci-dessous sont l'historique de l'étude.

Mise à jour du 2026-10-02 : étude dans `A3_PRIORITES.md`. La conversion
RGB444 ne conserve pas les règles de transparence GB : changer seulement
la priorité OAM ne suffit pas. Le chargeur conserve désormais indices
après flips et attributs source pour préparer l'allocation des masques.
Le rapport `background_priority` indique explicitement `not_emitted` quand
le décor utilise la priorité ; aucun masquage A3 n'est livré.
Sample Town officiel utilise déjà 16 cellules prioritaires : le refus
expérimental a été retiré. Les indications historiques ci-dessous sur
l'étude à faire sont remplacées par ce constat ; l'adaptation du tutoriel
reste à établir après une preuve de composition décor/sprites.


- **Dans GB Studio** : on peut peindre des tuiles qui passent **devant** les
  sprites.
- **Dans NGPCraft** : **à étudier dans la doc NGPC avant de choisir.** Ce
  qu'on sait : la NGPC ne règle pas la priorité tuile par tuile ; elle la
  règle **par sprite** (devant ou derrière les plans) et **entre les deux
  plans**. Piste de départ : mettre ces tuiles sur le plan qui est devant les
  sprites. À vérifier dans la doc (registres de priorité, `SPRITES_OAM.md`,
  `HW_REGISTERS.md`) et à confronter au partage des plans (texte §5,
  parallaxe §6).
- **Pourquoi** : c'est le matériel.
- **Adapter le tuto** : sera précisé une fois l'étude faite. (Décision NgpCraft, 2026-09-29 : voir avec la doc comment la NGPC le gère, puis
  l'adapter au mieux.)
- **État au 2026-09-30** : le même sujet existe côté sprites (case
  « priorité » d'une tuile de sprite dans l'éditeur GB Studio = sprite
  derrière les couleurs 1 à 3 du décor). Tant que ce point n'est pas tranché,
  une feuille qui l'utilise est **refusée à la compilation**, avec le nom de
  la feuille et le numéro d'image. Aucune feuille de `gbs2` ne l'utilise.

## 12. Fonctions propres au Game Boy — ✅

| Fonction GB Studio | Dans NGPCraft |
|---|---|
| Game Boy Printer (*Print*) | Absente, refusée à l'import |
| Super Game Boy (bordures, palettes, *If Device SGB*) | Absent : *If Device SGB* est toujours faux (livré 2026-10-09), le reste est refusé |
| *If Device GBA* | Toujours faux (livré 2026-10-09) |
| *If Color Supported* | Toujours vrai |
| Câble link (*Link Host / Join / Close / Transfer*) | Livré 2026-10-09 (émulateur, 2 consoles) : machine d'échange de GBVM sur l'UART du BIOS NGPC, trames numérotées avec reprise après perte (absente de GBVM) ; plus de 16 variables refusé ([M6_LINK](M6_LINK.md)) |
| Script GBVM brut (*GBVM Script*) | Accepté s'il n'utilise que des instructions prises en charge, sinon refusé avec la ligne du bloc en cause (livré 2026-10-09, `M4_GBVM_SCRIPT.md`) |
| Horloge de cartouche MBC3 (`VM_RTC_LATCH/GET/SET/START`, plugins d'horloge) | Livré 2026-10-13, adapté : la minuterie suit le calendrier de la console (jours depuis 1990, 9 bits) plus un décalage que le jeu règle ; **le réglage dure jusqu'à l'extinction** (une MBC3 le garde) ; valeurs hors plage ramenées dans la plage ([M8_NGPC_SYSTEM](M8_NGPC_SYSTEM.md)) |
| Types de cartouche (MBC), correction de couleurs LCD | Sans objet, ignorés |

## 13. Les fondus avancent par paliers réguliers — ✅

- **Dans GB Studio** : *Fade In*, *Fade Out* et le fondu automatique des
  scènes passent par 5 paliers vers le blanc (ou le noir, réglage *Fade
  Style*), au rythme choisi par la vitesse. Chaque palier allume (ou
  éteint) un bit de plus de chaque canal de couleur : la couleur change
  beaucoup au premier palier, puis de moins en moins.
- **Dans NGPCraft** : mêmes paliers, au même moment, même durée, même
  réglage *Fade Style*. Mais chaque palier rapproche chaque canal de la
  même quantité : du noir au blanc, `000 → 333 → 666 → 999 → CCC → FFF`.
- **Pourquoi** : la NGPC n'a que 16 niveaux par canal (4 bits). Avec la
  méthode de la Game Boy, deux paliers se confondaient : il ne restait que
  4 niveaux visibles et une chute brutale au début (`FFF → 777 → 333 → 111
  → 000`). Constaté sur console ; décision NgpCraft (2026-09-29). Le
  matériel ne sait pas faire de fondu lui-même : la doc K2GE ne propose
  qu'une inversion négative (`NEG`, 0x8012) et aucun appel BIOS ne touche
  aux palettes.
- **Adapter le tuto** : rien. Les scripts et les durées sont les mêmes ;
  seul l'aspect des étapes intermédiaires est plus régulier.

---

## 15. Cadence fixe à 30 images par seconde (option) — ✅ (2026-10-05)

- **Dans GB Studio** : le jeu calcule et affiche 60 images par seconde ;
  une scène trop chargée ralentit sans prévenir.
- **Dans NGPCraft** : par défaut, pareil (60). Option de projet
  `ngpcraft.json` → `"ngpc": {"frame_rate": 30}` : toute la logique de
  GB Studio tourne toujours à chaque image (scripts, joueur, déplacements,
  animations, texte), mais une image sur deux seulement est dessinée et
  affichée, à cadence fixe (deux images par tick). Le jeu va exactement à
  la même vitesse : mesuré, chaque image affichée à 30 est identique au
  pixel près à l'image correspondante à 60 (`tests/test_frame_rate.py`).
- **Pourquoi** : choix NgpCraft (2026-10-05), comme Over Rev : une
  cadence stable et choisie vaut mieux qu'un 60 qui saute des images. Le
  dessin coûte cher : le faire une fois sur deux libère le budget. Mesuré
  avec second décor et dialogues : 10 PNJ synchronisés, 14 décalés et les
  changements de page de dialogue tiennent sans perte à 30, pas à 60
  (`M2_PERF_PISTES.md` §3).
- **Adapter le tuto** : rien dans les scripts. Mouvements moins fluides à
  l'écran (une image sur deux). Les boutons sont lus 30 fois par seconde
  (les deux images logiques d'un tick voient le même état) : un appui plus
  court que deux images peut passer inaperçu.

## 16. Réglages du platformer écrits sur 16 bits — ✅ (2026-10-05)

- **Dans GB Studio** : `EVENT_ENGINE_FIELD_SET` écrit un réglage `UBYTE`
  ou `BYTE` (saut maintenu, coyote time, contrôle en l'air…) avec une
  instruction 8 bits (`VM_SET_CONST_INT8`, `VM_SET_INT8`).
- **Dans NGPCraft** : les variables du platformer sont sur 16 bits, parce
  que cc900 calcule les octets sur 8 bits là où le compilateur du Game Boy
  passe en 16 (`M3_PLATFORM.md`). Ces réglages sont donc écrits avec les
  instructions 16 bits (`VM_SET_CONST_INT16`, `VM_SET_INT16`). Les valeurs
  sont les mêmes ; seule la largeur de l'instruction change.
- **Pourquoi** : une écriture 8 bits dans une variable 16 bits perdrait le
  signe d'un `BYTE` négatif.
- **Conséquence visible (2026-10-09, [M6_ENGINE_FIELDS](M6_ENGINE_FIELDS.md))** :
  le moteur se comporte pareil pour toutes les valeurs que l'éditeur propose ;
  seul un **script** voit une différence hors de la plage d'un octet signé.
  *Engine Field Store* d'un réglage valant 200 (par exemple *Extra jumps*)
  rend 200 (GB Studio : −56, lecture 8 bits signée) ; *Engine Field Set*
  d'une valeur hors 0..255 la garde (GB Studio la tronque : 300 → 44).
  Concerne les 37 réglages `UBYTE`/`BYTE` du platformer, de l'aventure et du
  shmup, marqués `adapte` §16 dans le catalogue.
- **Adapter le tuto** : rien.

---

## En plus sur NGPC (extensions)

Disponibles en plus de GB Studio, sans rien changer à ce qui précède :
- second plan de défilement, devant ou derrière le décor principal
  (`second_background.behind`), avec bornes de caméra, tremblement, HUD,
  tuiles animées, palettes et sprites prioritaires ;
- 16 palettes par plan et une palette par sprite ;
- priorité des sprites par rapport aux plans ;
- effets raster ;
- horloge temps réel (livrée 2026-10-13 : *NGPC : Lire l'horloge*) et alarme (à faire) ;
- langue de la console (livrée 2026-10-13 : *NGPC : Si langue de la console*), tension des
  piles et extinction ([M8_NGPC_SYSTEM](M8_NGPC_SYSTEM.md)) ;
- code C natif dans un plugin.

Chacune aura sa page dédiée.

---

## Partage des deux plans — ✅ principe décidé (2026-10-01), conception à faire

Décision NgpCraft : **tout dépend du cas de figure**, scène par scène. Le
texte, qu'il s'agisse de dialogue, de HUD ou de menu, s'affiche **sur un plan
SCR**. Les menus en sprites (scène dédiée, ou sprites effacés le temps du
menu) existent en code manuel mais ne sont pas pratiques à adapter : ils ne
sont pas retenus pour l'adaptation des projets GB Studio.

Les décisions du 2026-09-29 mettent plusieurs usages sur les deux mêmes plans
de défilement (SCR1, SCR2) :
- le décor de la scène ;
- le texte, les menus et le HUD (§5) ;
- le second décor, extension NGPC (§6) ;
- les tuiles prioritaires (§11).

Il faudra une règle d'attribution par scène, calculée à la compilation et
affichée dans l'éditeur, avec le recours au raster quand deux usages doivent
partager un plan. **Attribution codée le 2026-10-02** : décor SCR2 derrière,
complément SCR1 devant, rapport de cas A/B par scène. Texte, masquage des
sprites et partage raster restent à faire (`A2_PLANS.md` §10). **Conception proposée le
2026-10-01 : `A2_PLANS.md`** (décor derrière, texte devant ; cas A, B, C).



## 14. Couleur transparente du décor — ✅ proposition retenue (2026-10-01, « au mieux »)

- **Dans GB Studio** : les quatre indices d'une tuile de fond sont opaques.
- **Dans NGPCraft** : l'indice 0 est transparent. Proposition : remapper les
  couleurs à la compilation avec un fond commun ; utiliser un complément
  sur le second plan uniquement lorsque nécessaire et compatible avec les
  autres usages. Le placement statique est intégré au moteur depuis le
  2026-10-02 : décor derrière, complément devant. Le partage avec le texte
  n'est pas encore livré (`A2_PLANS.md` §10).
- **Pourquoi** : trois couleurs opaques par palette de plan sur NGPC.
  L'étude corrigée de `gbs2` trouve 15 scènes représentables sur un plan,
  deux avec complément (45 et 141 tuiles). Voir `M2_DECORS_COULEURS.md`.
- **Adapter le tuto** : aucune retouche de dessin proposée par défaut ;
  l'import devra annoncer les budgets et les conflits de plans. Les
  palettes dynamiques et les priorités restent à traiter avant livraison.


## 17. Bordures du tremblement de caméra — implémenté, P5 en attente (2026-10-05)

`EVENT_CAMERA_SHAKE` garde le callback original `camera_shake_frames` :
axes, durée sur horloge VBlank 16 bits, offsets signés, retour à zéro.
Le générateur aléatoire reproduit la récurrence GBDK, sans importer son
assembleur. À la bordure de carte, V3 affiche la couleur de fond hors
du monde ; les lectures hors carte et les anciennes cellules de la couronne
GB ne sont pas reproduites. Décor et sprites utilisent le même décalage ;
physique et activation utilisent la caméra de base.

Première tranche : magnitude entière constante de 1 à 255, expression
constante comprise, ou constante de projet (2026-10-10 : sa valeur est
vérifiée, son symbole reste dans le bytecode comme chez GB Studio).
Magnitude dynamique ou nulle : refus explicite ; un
second décor à défilement indépendant est aussi refusé avec le shake.
Les scripts finis de collision réservent leur durée dans le budget.
Les horloges existantes des animations et de la physique ne changent pas.
La limite de cadence à 30 fps reste celle du §15 : deux pas de logique
entre images ; les deux appels voient la même horloge physique.
Preuves, ROM console et limites : `M3_CAMERA_SHAKE.md`. P5 du nouveau
binaire en attente ; la validation de l'ancien platformer ne vaut pas
validation du nouveau compteur VBlank.

## 18. Générateur aléatoire : même suite, autre graine de départ — ✅ (2026-10-06)

`EVENT_VARIABLE_MATH` (aléatoire), `EVENT_RNG_SEED`, l'opérande aléatoire
des valeurs calculées et le tremblement de caméra partagent **une seule
graine**, comme le `__rand_seed` de GBDK. La récurrence de GBDK 4.3.0
(`libc/asm/sm83/rand.s`) est réécrite en C indépendant, sans code GBDK :
`graine = 17 × graine + 0x5c93` ; `rand` rend le nouvel octet haut,
`randw` rend l'octet bas au-dessus de l'octet haut (convention `OLDCALL`,
paire DE), `initrand` pose la graine. Mêmes tirages que la Game Boy à
graine égale (vérifié en ROM : une seule graine sur 65 536 explique les
suites observées).

Deux écarts, déclarés :
- **Graine au démarrage** : GBVM fait `initrand(DIV_REG)` au boot ; V3
  part de zéro (déterministe). Elle n'est remise à zéro qu'au démarrage du
  moteur ; un changement de scène la conserve, comme sur Game Boy.
- **`EVENT_RNG_SEED`** (`VM_RANDOMIZE` : `DIV_REG + game_time × 256`) :
  `DIV_REG`, compteur libre à 16 384 Hz de la Game Boy, n'existe pas ; V3
  lit à sa place la ligne d'affichage courante RAS.V (0x8009, 0 à 198),
  valeur infra-image lue au même instant. `game_time` est l'octet bas du
  compteur de ticks du moteur de scène.


## 19. Remplacement de tuiles animées — implémenté sur émulateur (2026-10-07), P5 en attente

- **Dans GB Studio** : `EVENT_REPLACE_TILE_XY_SEQUENCE` écrit une tuile du
  tileset dans la tuile VRAM utilisée par la carte à la position demandée.
  Toutes les cellules partageant cette tuile changent, avec leurs propres
  palettes et retournements. La variable de séquence est incrémentée puis
  rebouclée ; son octet bas choisit la tuile, comme `vm_replace_tile_xy`.
- **Dans NGPCraft** : les variantes NGPC sont préparées à la compilation.
  Les cellules partageant les mêmes données GB et banque VRAM reçoivent
  les écritures correspondantes ; palettes et retournements sont conservés.
  Les coordonnées doivent être constantes. Un tileset inconnu est refusé
  au lieu du repli sur le premier. Un indice hors tileset laisse la tuile
  inchangée, puis le script poursuit l'incrément et le rebouclage originaux.
  GBVM lirait des octets hors du tileset dans ce cas : cette lecture n'est
  pas reproduite. La réunion des variantes doit tenir dans trois couleurs
  opaques plus le fond commun ; sinon l'export refuse le cas. Second décor,
  chemin de priorité A3 sans complément A4 et modification de l'opacité
  d'une cellule prioritaire : refus explicites, ces combinaisons ne sont
  pas livrées. `EVENT_REPLACE_TILE_XY` reste hors du catalogue.
- **Pourquoi** : les tuiles NGPC sont compilées depuis les couleurs et non
  les indices GB ; elles partagent 512 emplacements avec les sprites et
  le texte. Les variantes maintiennent les équivalences GB sans modifier
  une tuile statique identique qui n'appartient pas au même groupe GB.
- **Adapter le tuto** : garder des coordonnées constantes et un tileset
  valide. Prévoir une variable initialisée dans la plage de la séquence.
  Les trois séquences de Sample Town sont exercées en ROM. Les images
  proches des écritures VRAM ne sont pas couvertes par la comparaison
  pixels actuelle ; validation console et synchronisation des écritures
  restent à obtenir. Détails : [M4_REPLACE_TILES](M4_REPLACE_TILES.md).

## 20. Expressions invalides et neg() — décidé (2026-10-08)

**Dans GB Studio :** le helper ScriptValue remplace une expression invalide par zéro. Evaluate Expression compile neg(x) en SUB binaire avec un seul opérande.

**Dans NGPCraft :** diagnostic explicite et localisé pour les expressions invalides ; neg(x) refusé dans Evaluate Expression. Les expressions valides gardent les mêmes tokens, priorités et instructions.

**Pourquoi :** aucune modification silencieuse du calcul et aucune lecture hors pile.

**Adapter le tuto :** corriger la syntaxe ; écrire -x dans Evaluate Expression. Le helper ScriptValue conserve neg(x) sous la forme 0-x. Fonctions GBDK dynamiques et constantes non livrées restent refusées. Voir M4_EXPRESSIONS.md.


## 21. Indice de flag invalide — refus explicite (2026-10-08)

`eventIfVariableFlagsCompare.js` construit un masque seulement pour les valeurs
numériques 0 à 15, comparées avec `===`. Un indice absent, texte ou hors plage
produit silencieusement un masque zéro et une condition toujours fausse.
NgpCraft refuse ces indices avec localisation de l'événement. Les indices
numériques entiers 0 à 15 conservent les émissions originales, y compris le
bit 0x8000 et les paramètres indirects. Tests et contrat : M4_VARIABLE_FLAGS.

## 22. Un projectile ignore les acteurs hors de son masque — décidé (2026-10-08)

**Ce que fait GB Studio** : `projectiles.c` prend le premier acteur qui
chevauche le projectile (`actor_overlapping_bb`, en partant du joueur), puis
teste son groupe contre le masque. Un acteur hors du masque, au premier rang
le joueur collé à son propre coup, cache donc tous les autres : le coup ne
touche rien.

**Ce que fait la NGPC** : les acteurs hors du masque sont sautés ; le premier
acteur du masque qui chevauche est touché. Tir réservé au joueur inchangé.

**Pourquoi** : une attaque au corps à corps (projectile immobile près du
joueur, forme habituelle et celle que produit la migration de l'arme 2.0) doit
toucher l'ennemi qu'elle recouvre. Trouvé avec Dead Dungeon : il fallait
plusieurs coups pour tuer un ennemi qui meurt au premier.

**Preuve** : `tools/lab_projectile_pool.py`, oracle = `projectiles.c` publié
avec ce seul changement nommé (`ngc_overlapping_bb_in_mask`) ; témoin
`first-overlap` (règle GBVM d'origine) détecté. Dead Dungeon : le Slug meurt
au premier coup.

## 23. Masquer les sprites coupe leur priorité — décidé (2026-10-09)

**Ce que fait GB Studio** : *Hide Sprites* met `hide_sprites` à vrai ; à chaque
retour de balayage vertical, `interrupts.c` éteint la couche sprites du Game
Boy (bit OBJ de `LCDC`). *Show Sprites* la rallume. Le jeu continue de tourner.

**Ce que fait la NGPC** : le K2GE n'a pas d'interrupteur global des sprites.
Tant que le drapeau est vrai, la copie de l'image vers la mémoire des sprites
est remplacée par la mise à zéro de la priorité (PR.C = 0) des 64 sprites ;
les acteurs continuent d'être calculés. *Show Sprites* : la copie suivante
remet tout (elle couvre chaque emplacement utilisé).

**Pourquoi** : même résultat à l'écran, même moment (balayage vertical), aucun
coût hors de l'état masqué.

**Preuve** : `tools/lab_batch2.py` lit la mémoire des sprites en émulation :
0 sprite visible masqué, des sprites visibles après *Show Sprites*.

## 24. *Weapon Attack* : un événement que GB Studio 4.3.2 ne compile plus — refusé (2026-10-09)

**Ce que fait GB Studio** : `EVENT_WEAPON_ATTACK` (2.0, « deprecated ») appelle
`weaponAttack`, qui n'existe plus dans le compilateur 4.3.2 publié : la
compilation d'un projet qui le contient échoue (« Compiling EVENT_WEAPON_ATTACK
failed »). La migration des projets 2.0 r13 → r14 le remplace par *Launch
Projectile* (`migrateLegacyProjectVersions.ts`) : un projet 2.0 ancien ouvert
dans GB Studio, ou passé par `tools/migrate_gbstudio.py`, n'en contient plus.

**Ce que fait NgpCraft** : refus nommé à l'import, avec la solution. Un
projet qui en contient encore a été enregistré après la r14 sans migration
(Baldi's All-Stars, 3.1.0, dans le corpus) : GB Studio 4.3.2 ne le compile
pas non plus.

**Adapter le tuto** : remplacer l'événement par *Launch Projectile* (même
sprite, acteur source, décalage initial, durée de vie courte), comme le fait
la migration de GB Studio.

## 25. *Invoke Actor Script* appelle l'acteur nommé — décidé (2026-10-09)

**Ce que fait GB Studio 4.3.2** : `EVENT_ACTOR_INVOKE` (2.0, « deprecated »,
caché dans l'éditeur 4.x) appelle `actorInvoke`, qui lit
`scene.actors[actorIndex]` alors que `actorIndex` compte le joueur comme 0 :
il appelle le script d'interaction de l'acteur **suivant** dans la liste
(vérifié en exécutant le scriptBuilder publié : `a` → script de `b`,
joueur → script du premier acteur, dernier acteur → rien).

**Ce que fait NgpCraft** : le script d'interaction de l'acteur **nommé**, ce
que dit l'événement et ce que voulaient les projets 2.0 qui l'utilisent
(seuls ceux-là peuvent le contenir). Même mécanisme que GB Studio : appel
lointain au script `<acteur>_interact`, qui se termine par `VM_STOP` : le
script appelant s'arrête là. Joueur ou acteur sans script : rien. Chaque
usage produit un avertissement localisé.

**Adapter le tuto** : rien. Un projet qui comptait sur le décalage de GB
Studio 4.3.2 (improbable) doit nommer l'acteur suivant.

## 26. Emplacements de projectiles : vérifiés et vides sans effet — décidé (2026-10-09)

**Ce que fait GB Studio** : *Load Projectile Slot* recopie une définition
(rangée dans des tables globales de cinq) dans `projectile_defs[slot]`, les
cinq définitions de la scène ; *Launch Projectile Slot* tire celle de
l'emplacement. L'emplacement n'est pas vérifié (au-delà de 4, GBVM écrit
hors du tableau) ; un emplacement jamais rempli dans la scène tire ce qui y
traîne (définition d'une scène précédente). Chargement de scène (et *Load
Data*) : les définitions de la scène reviennent, les chargements sont perdus.

**Ce que fait NgpCraft** : même résultat pour tout emplacement rempli,
y compris le remplacement d'un type de la scène (un *Launch Projectile*
normal de ce type tire alors la définition chargée, comme GBVM) et la remise
à zéro au chargement de scène. Toutes les définitions sont connues à la
compilation : un emplacement est une table « emplacement → définition » en
RAM, sans copie. Emplacement hors 0..4 : refus à la compilation. Emplacement
vide : rien n'est tiré.

**Adapter le tuto** : rien, sauf un jeu qui tirerait volontairement un
emplacement jamais chargé dans la scène (comportement non défini dans GB
Studio).

## 27. Scènes plus grandes que 255 tuiles — en cours (2026-10-12)

**Ce que fait GB Studio** : une scène fait au plus 255 × 255 tuiles. Les
positions sont en 1/32 de pixel sur 16 bits (2 048 px = 256 tuiles), les
tuiles sur un octet.

**Ce que fait NgpCraft** : une scène peut faire jusqu'à **4 095 tuiles par
axe** et **65 535 tuiles en tout** (2 048 × 32, 1 024 × 64, 512 × 128,
255 × 255…). Rien à cocher : dès qu'une scène dépasse 255 tuiles, tout le jeu
passe aux positions sur 32 bits (même unité, mêmes vitesses ; coût mesuré
+1 % de calcul par image). Un jeu dont toutes les scènes font 255 tuiles ou
moins compile exactement comme avant. Dans un tel jeu « large » :
- les variables de position des scripts sont en **pixels** (jusqu'à 32 760) ;
  *Store Actor Position* et les conditions de position donnent les mêmes
  valeurs en tuiles et en pixels que GB Studio ;
- une valeur de script « position en sous-pixels » est refusée (elle ne tient
  plus sur 16 bits) ; un script GBVM écrit à la main ou un plugin qui écrit
  directement une instruction de position est refusé ;
- *Actor Effects* (split in/out) avance par pixels entiers : mêmes positions
  de départ et d'arrivée, étapes intermédiaires arrondies au pixel ;
- deux scripts qui déplacent le **même** acteur en même temps partagent sa
  cible (une par acteur, au lieu d'une par script) ;
- *Replace Tile* reste limité aux tuiles 0..255 (son instruction GBVM porte
  X et Y sur un octet) ;
- encore refusé dans un jeu large : le plugin Platformer+, et les tuiles
  prioritaires sur un décor de plus de 2 048 px (`PLAN_GRANDES_CARTES.md` G6).

**Adapter le tuto** : rien pour une scène de 255 tuiles ou moins. Pour une
scène plus grande, préférer les tuiles ou les pixels aux sous-pixels.
