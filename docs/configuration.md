# Réglages

Les réglages d'une installation sont dans `<dossier des cours>/.moodle-bridge/config.json`. Le plus simple pour les modifier est de **relancer `install.py`** : il reprend l'installation existante. Pour aller plus loin, édite le fichier : c'est du JSON, et toute clé absente garde sa valeur par défaut. Lance ensuite `status` pour vérifier que le fichier est toujours lisible.

## Les cours

Chaque cours suivi a une entrée sous `courses`, repérée par son identifiant Moodle (`courses --json` les liste) :

```json
"courses": {
  "1234": {"dest": "Algèbre", "code": "ALG", "mode": "mirror", "name": "Algèbre linéaire"},
  "5678": {"dest": "Biologie/Cellulaire", "code": "BIO", "mode": "classify"},
  "9999": {"skip": true}
}
```

| Clé | Rôle |
|---|---|
| `dest` | dossier du cours, relatif au dossier des cours ; il peut déjà exister et contenir tes fichiers |
| `code` | nom court affiché dans le tableau de bord |
| `mode` | `mirror` : un sous-dossier par section Moodle. `classify` : dossiers par séance (`CM/CM01`, `TD/TD03`…), avec l'IA pour les cas ambigus |
| `skip` | `true` pour ne plus suivre ce cours |
| `routes` | envoyer certaines sections vers un autre dossier, par exemple `[{"sections": "^TP", "dest": "Info/TP", "code": "TP"}]` (expression régulière sur le nom de la section) |

`courses --add-all` ajoute d'un coup tous les nouveaux cours, dans le mode de `default_mode`.

## Le rangement par séance (`classify`)

| Clé | Rôle |
|---|---|
| `classify.rules` | tes conventions, en quelques phrases, données telles quelles à l'IA |
| `classify.new_dir_patterns` | dossiers que l'IA a le droit de créer (expressions régulières) ; les dossiers existants sont toujours permis |
| `classify.min_confidence` | en dessous de ce niveau (0,8 par défaut), le fichier va dans `_a_classer/` |
| `classify.inbox` | nom du dossier d'attente (`_a_classer`) |
| `overrides` | corrections durables : `"<cours>/<activité>/<fichier>": "TD/TD07"`, avec la clé lue dans `state.json` |

Le moteur d'IA lui-même est décrit dans [ia.md](ia.md).

## Ce qui est téléchargé

| Clé | Défaut | Rôle |
|---|---|---|
| `exclude.list_only_mimetypes` | `["video/", "audio/"]` | listés dans le tableau de bord avec leur lien Moodle, jamais téléchargés |
| `exclude.max_size_mb` | `200` | fichiers plus lourds seulement listés |
| `exclude.modnames` | pages, livres, leçons, SCORM… | activités web ignorées comme fichiers ; le texte des pages est quand même conservé |

## Le coffre Obsidian

| Clé | Défaut | Rôle |
|---|---|---|
| `vault.dir` | `Tableau de bord` | dossier des notes générées, relatif au dossier des cours (`null` : aucun coffre) |
| `vault.title` | `Moodle` | titre de la note d'accueil |
| `index.news_days` | `7` | durée d'affichage d'un nouveau fichier et d'une annonce dans l'accueil |
| `index.announcements_per_course` | `5` | annonces récentes relevées par cours |

Le coffre contient `Accueil`, une note par matière (`Matières/`), une par devoir (`Devoirs/`), puis `Échéances`, `Annonces`, `Documents`, `Modalités de contrôle` et `Notes`. Il est écrit sans IA : voir [Lire tes données](../README.md#lire-tes-données--le-coffre-obsidian).

- **Une matière** correspond à un cours suivi, avec son `code`. Une route (`routes`) qui envoie des sections hors du dossier du cours devient une matière à part : par exemple les séances d'insertion professionnelle hébergées dans le cours Moodle d'un autre module. Une route vers un sous-dossier du cours reste dans la même matière.
- **Les liens vers les fichiers** sont relatifs quand le fichier est dans le coffre Obsidian (le dossier, ou son premier parent, qui contient `.obsidian`) : Obsidian les ouvre lui-même. Sinon, ce sont des liens `file://`.
- `vault.dir` peut être hors du dossier des cours (chemin absolu, ou `../Mon coffre/Moodle`) : par exemple un coffre Obsidian réservé à Moodle, à côté de tes cours.

### L'ancien index et les textes

| Clé | Défaut | Rôle |
|---|---|---|
| `index.path` | `null` | ancien tableau de bord en un seul fichier (`Moodle.md`), conservé pour les installations antérieures au coffre |
| `texts.dir` | `.moodle-bridge/textes` | un fichier Markdown par cours avec ses textes bruts ; le coffre les reprend déjà dans les notes des matières |
| `texts.mcc_title`, `texts.mcc_content` | motifs français | repérage des modalités de contrôle, à adapter pour un Moodle dans une autre langue |
| `texts.enabled` | `true` | `false` : ne conserver aucun texte |

## La synchronisation automatique

| Clé | Défaut | Rôle |
|---|---|---|
| `schedule.interval_minutes` | `120` | intervalle entre deux passages |
| `notify` | `true` | notifications du système (nouveaux fichiers, jeton expiré, modalités de contrôle modifiées) |

Après avoir changé l'intervalle, réinstalle la tâche : `schedule install`. `schedule uninstall` arrête la synchronisation automatique ; `schedule kick` lance un passage tout de suite.

## Pour les agents IA

La liste complète des clés, avec les supports de cours envoyés vers Zotero, est dans [skill/references/fonctionnement.md](../skill/references/fonctionnement.md).
