# API Moodle utilisée par le pont

Le pont n'utilise que des fonctions **en lecture** du service web de l'appli mobile (`moodle_mobile_app`). Toutes passent par `POST <site>/webservice/rest/server.php` avec `wstoken`, `wsfunction` et `moodlewsrestformat=json` dans le corps de la requête.

| Fonction | Usage |
|---|---|
| `core_webservice_get_site_info` | identité de l'utilisateur, vérification du jeton |
| `core_enrol_get_users_courses` | cours où l'utilisateur est inscrit |
| `core_course_get_contents` | sections, activités et fichiers (ressources, dossiers) ; textes : `summary` des sections, `description` des étiquettes et activités |
| `mod_assign_get_assignments` | devoirs : dates limites, pièces jointes et consignes (`intro`, `activity`, `timemodified`) |
| `mod_page_get_pages_by_courses` | texte des pages (`intro`, `content`, `timemodified`) |
| `core_calendar_get_calendar_upcoming_view` | échéances à venir |
| `mod_forum_get_forums_by_courses`, `mod_forum_get_forum_discussions` | dernières annonces (forums « news ») et leur texte (`message`, `created`, `modified`) |

Les textes arrivent en HTML ; leurs images et pièces jointes pointent vers `…/webservice/pluginfile.php/…` (parfois avec `?token=`). Le pont les convertit en Markdown et retire le jeton de toute URL avant d'écrire quoi que ce soit.

Téléchargement : `POST` sur l'URL `…/webservice/pluginfile.php/…` fournie par l'API, avec `token` dans le corps. Pour l'index, ces URL sont converties en `…/pluginfile.php/…`, ouvrables dans un navigateur déjà connecté.

Configuration publique, sans identifiant : `POST <site>/lib/ajax/service-nologin.php?info=tool_mobile_get_public_config` avec `[{"index":0,"methodname":"tool_mobile_get_public_config","args":{}}]` (commande `check-site`).

## Obtenir un jeton

| Méthode | Quand | Comment |
|---|---|---|
| Page « Clés de sécurité » (`token --method key`) | l'établissement affiche les clés de services web | `<site>/user/managetoken.php`, ligne « Moodle mobile web service » |
| Connexion de l'appli (`launch.php`, `token --method browser`) | connexion SSO (`typeoflogin` 2 ou 3) — Shibboleth, CAS… | ouvre `admin/tool/mobile/launch.php?service=moodle_mobile_app&passport=…&urlscheme=moodlemobile` ; la redirection `moodlemobile://token=<base64>` contient `md5(site+passport):::jeton[:::jeton privé]` |
| `login/token.php` (`token --method password`) | comptes Moodle classiques (`typeoflogin` 1) | `POST username`, `password`, `service=moodle_mobile_app` ; le mot de passe n'est jamais enregistré. Inutilisable avec une connexion SSO (`invalidlogin`) |

La clé RSS de la page « Clés de sécurité » a le même format (32 caractères hexadécimaux) mais ne fonctionne pas avec l'API : Moodle répond `invalidtoken`.

`typeoflogin` (configuration publique) : 1 = formulaire dans l'appli, 2 = navigateur externe, 3 = navigateur intégré.

## Erreurs fréquentes

| Code | Sens | Action |
|---|---|---|
| `invalidtoken` | jeton inconnu (mauvaise clé, réinitialisée, révoquée) | nouveau jeton (`token`) |
| `invalidtimedtoken` | jeton expiré | nouveau jeton |
| `invalidiptoken` | jeton restreint à une autre adresse IP | nouveau jeton depuis ce réseau |
| `accessexception` / `nopermissions` | fonction non autorisée pour ce compte | la fonction est ignorée (devoirs, pages, calendrier ou annonces manquants dans l'index) ; les textes déjà conservés pour ce type sont gardés |
| `sizemismatch` | taille reçue différente de celle annoncée | nouvel essai au passage suivant |
| `CERTIFICATE_VERIFY_FAILED` | Python sans certificats racines | macOS : lancer « Install Certificates.command » du dossier Python des Applications (le client essaie aussi `/etc/ssl/cert.pem`) ; Windows et Linux : certificats du système |

Les erreurs réseau et 5xx sont réessayées trois fois (10 s, 30 s, 90 s) : au réveil de l'ordinateur, le Wi-Fi n'est pas toujours prêt.

## Limites connues

- Liens externes (Google Drive, sites d'enseignants), activités « URL » : non récupérés.
- Pièces jointes des annonces : signalées par 📎 dans l'index, non téléchargées.
- Pages : texte conservé (voir « Textes et modalités de contrôle » dans `fonctionnement.md`), jamais téléchargées comme fichiers. Livres, leçons, SCORM, H5P : ignorés (contenu web, pas des documents).
- Résumés de sections et étiquettes : Moodle ne fournit pas leur date de modification ; le pont date ce qu'il constate.
- Annonces : seules les dernières de chaque cours (`index.announcements_per_course`) sont interrogées ; les plus anciennes déjà relevées restent dans l'état.
- Un cours caché ou dont l'inscription a expiré disparaît de `core_enrol_get_users_courses` : ses fichiers passent en « retiré de Moodle », les copies locales restent.
- La durée de validité d'un jeton dépend des réglages de l'établissement.
