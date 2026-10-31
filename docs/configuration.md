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
| `mode` | `mirror` : un sous-dossier par section Moodle (conseillé dans cette édition). `classify` : rangement selon ton arborescence existante, voir ci-dessous |
| `skip` | `true` pour ne plus suivre ce cours |
| `routes` | envoyer certaines sections vers un autre dossier, par exemple `[{"sections": "^TP", "dest": "Info/TP", "code": "TP"}]` (expression régulière sur le nom de la section) |

`courses --add-all` ajoute d'un coup tous les nouveaux cours, dans le mode de `default_mode`.

## Le rangement dans ton arborescence (`classify`)

Cette édition n'a pas d'IA. En mode `classify`, un nouveau fichier est rangé seulement par des règles sûres :
- un fichier identique existe déjà dans le dossier du cours : il reste où il est ;
- une correction (`overrides`) lui impose un dossier ;
- au moins deux fichiers déjà rangés de la même activité Moodle sont dans le même dossier : il les rejoint.

Sinon, il va dans `_a_classer/`. Déplace-le à la main : la synchronisation suivante le retrouve par son empreinte, et ses voisins suivront.

| Clé | Rôle |
|---|---|
| `classify.inbox` | nom du dossier d'attente (`_a_classer`) |
| `overrides` | corrections durables : `"<cours>/<activité>/<fichier>": "TD/TD07"`, avec la clé lue dans `state.json` |

## Ce qui est téléchargé

| Clé | Défaut | Rôle |
|---|---|---|
| `exclude.list_only_mimetypes` | `["video/", "audio/"]` | listés dans le tableau de bord avec leur lien Moodle, jamais téléchargés |
| `exclude.max_size_mb` | `200` | fichiers plus lourds seulement listés |
| `exclude.modnames` | pages, livres, leçons, SCORM… | activités web ignorées comme fichiers ; le texte des pages est quand même conservé |

## Le tableau de bord et les textes

| Clé | Défaut | Rôle |
|---|---|---|
| `index.path` | `Moodle.md` | emplacement du tableau de bord (`null` : aucun) |
| `index.news_days` | `7` | durée d'affichage d'un nouveau fichier dans « Nouveautés » |
| `index.announcements_per_course` | `5` | annonces récentes relevées par cours |
| `texts.dir` | `Textes Moodle` | un fichier Markdown par cours avec ses textes |
| `texts.mcc_title`, `texts.mcc_content` | motifs français | repérage des modalités de contrôle, à adapter pour un Moodle dans une autre langue |
| `texts.enabled` | `true` | `false` : ne conserver aucun texte |

## La synchronisation automatique

| Clé | Défaut | Rôle |
|---|---|---|
| `schedule.interval_minutes` | `120` | intervalle entre deux passages |
| `notify` | `true` | notifications du système (nouveaux fichiers, jeton expiré, modalités de contrôle modifiées) |

Après avoir changé l'intervalle, réinstalle la tâche : `schedule install`. `schedule uninstall` arrête la synchronisation automatique ; `schedule kick` lance un passage tout de suite.

## Toutes les clés

La liste complète, avec les supports de cours mis de côté pour Zotero, est dans [architecture.md](architecture.md#réglages).
