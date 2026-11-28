# Fiscalité (France, particuliers) — aide, pas une déclaration

Module : `backend/accounting/fiscal.py`, endpoint `/api/export/fiscal?year=AAAA`, export CSV `/api/export/operations.csv`.

- Fait générateur : cession contre euros (ou bien/service). Échanges crypto-crypto sans soulte : sursis.
  Le robot ne trade que des paires EUR → **chaque vente est une cession imposable**.
- Exonération si le total annuel des **prix de cession** ≤ 305 € (pas le bénéfice). Avec un trading actif,
  ce seuil est vite dépassé même avec 100 € de capital.
- Plus-value par cession : `PV = C − PTA × C / V` (C prix de cession net de frais, PTA prix total
  d'acquisition résiduel, V valeur globale du portefeuille juste avant la cession). Le module calcule ces lignes
  pour le formulaire **2086**. Limite : V est approximé par les positions du robot ; les autres actifs
  numériques du foyer doivent être ajoutés.
- Compte à l'étranger (Bitvavo B.V., Pays-Bas) : déclaration **3916-bis** à vérifier chaque année.
- La qualification d'une activité algorithmique fréquente (régime des particuliers vs BNC/professionnel)
  dépend de la situation : faire vérifier par un professionnel. Le P&L du robot n'est pas une déclaration.
