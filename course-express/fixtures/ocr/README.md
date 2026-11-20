# Fixtures de texte OCR

- `uber_pizza_cosy*.txt` : transcription **manuelle** de `../uber_offre_pizza_cosy.jpeg`, dans l'ordre de lecture
  (haut → bas) que produit `ScreenshotOCR.swift`. La sortie réelle de Vision peut varier légèrement : sur iPhone,
  lancer le test `OfferParserFixtureImageTests` (target iOS) qui passe la vraie image par Vision.
- `uber_no_address.txt` : variante dégradée (adresse absente) pour vérifier l'erreur explicite.
- `SYNTHETIQUE_deliveroo_*.txt` : **fixtures synthétiques inventées**, faute de vraie capture Deliveroo.
  Le format réel Deliveroo reste **à valider** sur une vraie capture (et Deliveroo peut ne pas afficher l'adresse
  complète du client avant acceptation : dans ce cas l'app renverra « Adresse du client introuvable »).
