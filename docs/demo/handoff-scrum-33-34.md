# Handoff — SCRUM-33 & SCRUM-34 (doc + démo présentation)

> **But de ce fichier :** transférer cette session vers une autre conversation (ex. claude.ai).
> Colle tout ce fichier dans un nouveau chat pour reprendre le travail sans perdre le contexte.
> **Contexte perso :** Olivier, développeur Java à la base (expliquer les idiomes Python via analogies Java).
> Présentation de 45 min le **2026-07-16**. Périmètre gelé sur la doc + démo (cloud reporté).

---

## 1. Ce qu'on fait et pourquoi

Deux tickets Jira, traités **ensemble** parce qu'ils convergent vers le même livrable (README + GIF + démo) :

- **SCRUM-33** — `RUNBOOK.md` (archi, deploy, debug, coûts + diagramme + commandes) et le **README** principal
  (actuellement `README.md` = 2 lignes, `RUNBOOK.md` n'existe pas encore).
- **SCRUM-34** — run propre de `demo.sh`, GIF + captures Grafana, **script de démo live 5 min**, intégrés au README.

**Méthode choisie : l'interview.** Objectif → que le contenu vienne d'Olivier (donc qu'il puisse le défendre
en live devant un jury), et que Claude fasse seulement la mise en forme. C'est une présentation défendue en
direct : si Claude rédige tout, Olivier récite un texte qu'il ne maîtrise pas et coule à la première question.

### Partage du travail
| Claude extrait tout seul (factuel) | On interviewe Olivier (narratif / jugement) |
|---|---|
| Liste des commandes fréquentes | L'arc de la démo 5 min |
| Breakdown des coûts (depuis `demo.sh`) | Le « pourquoi » des choix techniques |
| Diagramme d'archi (Mermaid) | Ce que le jury doit retenir |
| Étapes de setup / deploy | Les réponses aux questions probables du jury (Q&A anticipé) |

---

## 2. Le projet en bref (pour le Claude qui reprend)

Plateforme d'évaluation de LLM : compare Claude / OpenAI / DeepSeek / Gemini sur les mêmes prompts, mesure
qualité (LLM-as-judge Gemini, échelle 0–5), latence, tokens, coût ; persiste tout en Postgres ; détecte les
régressions en CI. Détails complets dans `docs/ARCHITECTURE.md`.

`demo.sh` (le run de démo, ~10-15 s) fait 4 étapes :
1. **Préflight** — santé Postgres + clés API présentes
2. **Sync prompts** — prompts YAML versionnés → BD
3. **Run parallèle** — 4 modèles (`claude-sonnet-4-6`, `claude-opus-4-8`, `gpt-5`, `o3`) × 6 cas, en parallèle (~10-15 s)
4. **Agrégats SQL** — breakdown par modèle + vue `run_metrics`

Dashboards Grafana dispo : comparaison modèles, benchmark de styles, contrôle du style de réponse,
triage qualité, **décision finale par profil d'usage** (SCRUM-38).

---

## 3. Interview — Round 1 (l'arc de la démo) — RÉPONDU

1. **Message d'une phrase** → « Comparer plusieurs modèles et automatiser les tests ; pouvoir facilement
   changer les modèles et les tests. »
2. **Moment "wow"** → « Faire des appels et pouvoir mettre toutes les réponses dans une base de données. »
3. **Ordre de la démo** → *ne savait pas* → Claude a proposé un arc (voir §5).
4. **Avant / après** → « Par curiosité, pouvoir comparer plusieurs modèles pour m'aider à choisir mon
   abonnement dans le futur. » (accroche perso et authentique — à garder)
5. **Fierté technique** → « Avoir complété l'adaptateur (API converter) de Gemini — première fois que je
   travaillais avec des API. »
6. **Risque live** → « Que ma démo ne montre rien de significatif, ou ne pas pouvoir répondre à une
   question sur l'architecture du projet. »

**Insight clé :** la peur #6 (répondre aux questions d'archi) est LE risque. Le Q&A anticipé (§6) l'adresse
directement.

---

## 4. Interview — Round 2 (les « pourquoi ») — EN ATTENTE DE RÉPONSE

Réponds en vrac, Claude met en forme. « Je sais pas » est une réponse valable → on débroussaille ensemble.

7. **Pourquoi une base de données** plutôt qu'imprimer les résultats ? (historique, comparer dans le temps,
   alimenter Grafana…)
8. **Pourquoi un LLM qui juge un autre LLM** — peut-on lui faire confiance ?
9. **Pourquoi Gemini comme juge** plutôt que Claude / GPT ?
10. **Le truc que tu comprends le moins** dans ton propre projet (honnêteté = le plus important).
    Candidats : OLS style-adjusted, échantillonnage répété, cache de décision.

---

## 5. Arc de démo 5 min proposé (à valider / corriger par Olivier)

| Temps | Écran | Angle |
|---|---|---|
| 0:00–0:30 | Slide/terminal | Accroche perso : « comparer des LLM pour choisir *mon* abonnement, sur mes prompts » |
| 0:30–1:30 | Terminal `bash demo.sh` | Run parallèle live : 4 modèles × 6 cas en ~10 s |
| 1:30–2:30 | `psql` / table d'agrégats | **Le wow** : chaque réponse persistée (latence, tokens, coût, note juge) |
| 2:30–4:00 | Grafana | Comparaison modèles → **décision finale par profil** |
| 4:00–4:30 | Dataset YAML / `MODELS=(...)` | « Changer de modèle ou de test = éditer une ligne » |
| 4:30–5:00 | Slide fin | Message d'une phrase + le CI qui bloque les régressions |

**Questions ouvertes sur l'arc :**
- L'ordre convient-il ? Inverser quelque chose ?
- Mettre un moment explicite « le juge tourne sur Gemini, l'adaptateur que j'ai codé » (fierté #5) — dans
  la démo ou plutôt dans le Q&A ?

---

## 6. Q&A anticipé (à construire à partir du Round 2)

À remplir avec les réponses d'Olivier aux questions 7-10, formulées comme des Q/R que le jury pourrait poser.
Objectif : neutraliser la peur #6.

---

## 7. Où on en est / prochaine étape

- [x] Tickets lus, méthode (interview) choisie, travail partagé
- [x] Round 1 répondu, arc de démo proposé
- [ ] **PROCHAINE ÉTAPE : Olivier répond au Round 2 (questions 7-10)**
- [ ] Claude rédige le 1er jet du script de démo 5 min à partir des réponses
- [ ] Claude construit le Q&A anticipé
- [ ] Claude extrait le factuel du RUNBOOK depuis le code (commandes, coûts, diagramme, setup)
- [ ] Assembler README + RUNBOOK, capturer GIF + screenshots Grafana
- [ ] Mettre à jour Jira (SCRUM-33/34 In Progress → In Review quand PR ouverte)

**Note pour le Claude qui reprend :** si tu es sur claude.ai, tu n'as PAS accès au code local ni au MCP Jira
d'Olivier. Demande-lui de coller les fichiers pertinents (`demo.sh`, `docs/ARCHITECTURE.md`, `README.md`) si
tu as besoin du contenu réel pour rédiger.
