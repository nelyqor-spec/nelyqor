# Checklist de tests réels (à remplir sur iPhone — ne pas inventer de chiffres)

Appareil : ……  iOS : ……  Réseau : 4G / 5G  Serveur : MOCK / Google réel  Date : ……

## Compilation
- [ ] `xcodegen generate` sans erreur
- [ ] Build des 3 targets sous Xcode (noter les corrections nécessaires : le code Swift n'a jamais été compilé)
- [ ] `CourseExpressTests` : `OfferParserTests` vert
- [ ] `OfferParserFixtureImageTests` (vraie capture via Vision) vert — sinon copier ici le texte OCR obtenu

## Fonctionnel
- [ ] Capture Uber Pizza Cosy → restaurant « Pizza Cosy - Aix en Provence », client « 230 avenue de Bagatelle… »
- [ ] Google réel : restaurant trouvé = 475 route de Berre, 13090 Aix-en-Provence (fixture de validation)
- [ ] Les deux trajets sont en voiture, la somme affichée est cohérente
- [ ] Vraie capture **Deliveroo** : nom + adresse lus (sinon joindre la capture pour adapter l'extracteur)
- [ ] Nom ambigu (chaîne) → liste « Corriger le restaurant » ; choix → résultat
- [ ] Localisation refusée → message clair, aucun chiffre
- [ ] Mode Avion → « Réseau indisponible », aucun chiffre
- [ ] Raccourci lancé **depuis Uber Eats au premier plan** : la position est-elle obtenue ? (sinon noter l'erreur affichée)

## Déclenchement
| Méthode | Fonctionne ? | Remarques |
|---|---|---|
| Bouton Action | | |
| Toucher le dos ×2 | | |
| AssistiveTouch – section Raccourcis présente ? | | |
| AssistiveTouch – Capture d'écran seule | ne lance pas l'analyse (attendu) | |

## Affichage
- [ ] Bannière de notification visible au-dessus d'Uber Eats
- [ ] Live Activity / Dynamic Island visible
- [ ] Avec un mode Concentration actif

## Latence (Réglages > Mesures réelles, après ≥ 30 analyses)
| Mesure | Valeur |
|---|---|
| Nombre d'analyses | |
| Taux de réussite | |
| p50 total | |
| p95 total | |
| Erreurs les plus fréquentes | |

Détail par étape : Console macOS, sous-système `CourseExpress`, lignes `analyse ok=… ocr=… gps=… net=… total=…`.
