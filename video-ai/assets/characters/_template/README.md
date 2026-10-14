# Fiche personnage

Personnage **fictif** : il ne doit ressembler à aucune personne réelle identifiable.

| Dossier | Contenu |
|---|---|
| `character.json` | Identité canonique (visage, yeux, peau, cheveux, morphologie), style, tenues, seeds retenus |
| `refs/face/` | Portraits de face validés : `<slug>_face_v<version>_<seed>.png`. La première image sert de référence pour `flux2_klein_4b_ref` |
| `refs/profile/` | Vues de profil validées |
| `refs/full-body/` | Vues en pied validées |
| `outfits/<tenue>/` | Rendus par tenue |
| `scenes/<scene>/` | Rendus par décor |
| `campaigns/<campagne>/` | Rendus et sélections par campagne |
| `generations.jsonl` | Journal automatique : workflow, prompt, seed, paramètres, sorties |

## Méthode de cohérence

1. **Portrait de référence.** Générer des portraits avec `vai character prompt <slug>` puis `vai run flux2_klein_4b_t2i`. Garder le meilleur dans `refs/face/` et noter son seed dans `character.json`.
2. **Vues dérivées.** Produire profil, pied, tenues et décors avec `flux2_klein_4b_ref`, à partir de la référence de face et du prompt canonique, plus la tenue ou la scène voulue.
3. **Prompt identique.** Toujours repartir du prompt canonique : on ne fait varier que la tenue, la scène et le cadrage.
4. **LoRA plus tard.** Une LoRA d'identité n'est envisagée qu'une fois au moins 20 références cohérentes sélectionnées. Elle n'est **pas automatique**.
