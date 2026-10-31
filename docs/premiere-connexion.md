# Première connexion à Moodle

Moodle Bridge utilise le **service web de l'appli mobile officielle** de Moodle. Il voit donc exactement ce que tu vois dans l'appli Moodle de ton téléphone, rien de plus. Pour y accéder, Moodle lui délivre un **jeton** : une clé de 32 caractères qui remplace ton identifiant et ton mot de passe.

## Où va le jeton ?

| Système | Coffre utilisé | Pour le voir ou le supprimer |
|---|---|---|
| macOS | Trousseau d'accès (session) | app *Trousseau d'accès*, recherche « moodle-bridge » |
| Windows | Gestionnaire d'identification Windows | *Panneau de configuration → Gestionnaire d'identification → Informations d'identification Windows* |
| Linux | trousseau de session (`secret-tool`, GNOME Keyring, KWallet…) ; à défaut, un fichier lisible par toi seul dans `~/.local/share/moodle-bridge/tokens/` | `secret-tool search service moodle-bridge…` |

Le jeton n'est écrit dans aucun autre fichier : ni dans les réglages, ni dans les journaux, ni dans le tableau de bord. `token --forget` le supprime.

Pour un usage avancé (serveur, conteneur), la variable d'environnement `MOODLE_BRIDGE_TOKEN`, si elle est définie, est utilisée à la place du coffre.

## Quelle méthode pour moi ?

L'assistant le détecte tout seul, d'après le type de connexion que ton Moodle annonce à l'appli mobile. Pour le savoir toi-même :

```bash
python3 scripts/moodle_bridge.py check-site --site https://moodle.mon-universite.fr
```

| Ce que tu fais pour te connecter à Moodle sur le web | Méthode |
|---|---|
| Tu tapes ton identifiant et ton mot de passe **sur la page de Moodle** | `password` |
| Tu es redirigé vers une **page de ton établissement** (CAS, ENT, Shibboleth, Microsoft, Google…) | `browser` |
| Ta page *Préférences → Clés de sécurité* montre une clé « Moodle mobile web service » | `key` (marche aussi avec les deux cas ci-dessus) |

Pour lancer ou relancer la connexion à tout moment :

```bash
python3 scripts/moodle_bridge.py token --config ~/Moodle/.moodle-bridge/config.json [--method password|browser|key]
```

Sous Windows, remplace `python3` par `py`. La commande se lance **dans ton terminal** : elle te pose des questions, et personne d'autre, agent IA compris, ne doit faire cette étape à ta place.

## Méthode `password` : identifiant et mot de passe Moodle

1. Saisis ton identifiant Moodle, puis ton mot de passe. Le mot de passe ne s'affiche pas pendant la frappe.
2. Moodle Bridge l'envoie une seule fois à ton Moodle, en HTTPS, à l'adresse `login/token.php` prévue pour l'appli mobile, et reçoit le jeton.
3. Le mot de passe est aussitôt oublié. Seul le jeton est rangé dans le coffre.

Si Moodle répond « identifiant ou mot de passe refusé » alors qu'ils sont justes, ton établissement passe sans doute par une page d'authentification unique : utilise `--method browser`.

## Méthode `browser` : par le navigateur

C'est la méthode de l'appli mobile quand l'établissement a sa propre page de connexion. Moodle Bridge prépare une adresse spéciale (`…/admin/tool/mobile/launch.php?…`). Ton navigateur, déjà connecté, l'ouvre. Moodle répond en redirigeant vers `moodlemobile://token=…`, une adresse destinée à l'appli. Ton navigateur ne sait pas l'ouvrir, mais elle reste visible dans les outils de développement : c'est elle qu'on récupère.

Fonctionne avec Chrome, Edge, Brave et Firefox.

1. **Connecte-toi à Moodle** dans ton navigateur, comme d'habitude.
2. **Ouvre les outils de développement** : `Cmd+Option+I` sur Mac, `F12` ou `Ctrl+Maj+I` sous Windows et Linux.
3. Va dans l'onglet **Réseau** (*Network*) et coche **Conserver le journal** (*Preserve log* ; Firefox : *Conserver les journaux*).
4. **Dans ce même onglet**, colle dans la barre d'adresse le lien affiché par le terminal, puis Entrée.
   - La page semble ne rien faire, ou affiche une erreur : c'est normal.
   - Si le navigateur propose d'ouvrir l'appli Moodle, **refuse**.
5. Dans le **filtre** de l'onglet Réseau, tape `token`. Une ligne `moodlemobile://token=…` apparaît. Fais un clic droit sur elle, puis *Copier → Copier l'URL*.
   - Si elle n'apparaît pas, clique sur la ligne `launch.php`.
   - Dans *En-têtes → En-têtes de réponse*, copie la valeur de `location`.
6. Reviens au terminal :
   - colle l'adresse, rien ne s'affiche et c'est normal, puis Entrée ;
   - ou appuie seulement sur Entrée : Moodle Bridge lit directement le presse-papiers.

Moodle Bridge vérifie que la réponse correspond bien à **cette** tentative grâce à une signature, enregistre le jeton, puis **vide le presse-papiers**.

Messages fréquents :
- *« C'est l'adresse launch.php donnée par le script »* : tu as copié l'adresse de départ. Il faut celle qui commence par `moodlemobile://`.
- *« Signature inattendue »* : l'adresse vient d'une tentative précédente. Relance `token` et refais l'étape 4.

## Méthode `key` : la clé de sécurité

1. Sur Moodle, ouvre ton **profil → Préférences → Clés de sécurité** (adresse `…/user/managetoken.php`).
2. Si une ligne a pour service **« Moodle mobile web service »**, copie sa clé de 32 caractères. La **clé RSS** a le même aspect mais ne fonctionne pas.
3. Lance `token --method key` et colle-la.

Beaucoup d'établissements n'affichent pas cette ligne. Utilise alors une des deux autres méthodes.

## Et après ?

Le jeton est vérifié auprès de Moodle : `✓ Connecté … en tant que …`. Sa durée de validité dépend de ton établissement, de quelques semaines à plusieurs années. Quand il expire :
- le tableau de bord affiche « jeton refusé » ;
- l'application macOS affiche un triangle ;
- une notification te prévient.

Il suffit alors de relancer `token`.

## Et si mon Moodle refuse l'appli mobile ?

`check-site` affiche « Le pont ne peut pas fonctionner : l'API de l'appli mobile est désactivée ». Seul ton établissement peut l'activer : si l'appli Moodle officielle ne fonctionne pas non plus sur ton téléphone, c'est la même cause.
