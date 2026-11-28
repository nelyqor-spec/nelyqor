"""Intelligence événementielle (phase 6) — module AUTONOME, sans influence sur les décisions de trading.

Il collecte des actualités et signaux publics gratuits (sans clé), les normalise (`model`), les nettoie
(`safety`), les relie entre eux (`hygiene` : doublons, recyclage, corroboration, rumeur, source officielle),
les journalise en AJOUT SEUL (`journal`) et MESURE les sources (`metrics`) ainsi que la réaction du marché
APRÈS coup (`market`, horizons 1 h / 4 h / 24 h, jamais de valeur future au moment de la réception).

Tout contenu externe est une DONNÉE NON FIABLE : jamais exécutée, jamais interprétée comme une instruction,
jamais rendue en HTML ; les liens des articles sont conservés mais jamais suivis.
"""
