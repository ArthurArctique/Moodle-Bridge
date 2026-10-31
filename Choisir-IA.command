#!/bin/zsh
# Moodle Bridge : choisir l'IA (Claude Code, Codex, autre commande ou aucune) et son modèle.
# Double-clic dans le Finder. Même chose dans un terminal : python3 scripts/moodle_bridge.py ai
cd "${0:A:h}" || exit 1
python3 -I scripts/moodle_bridge.py ai "$@"
echo
read -k 1 "?Appuie sur une touche pour fermer cette fenêtre."
