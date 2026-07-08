-- 019 — Retrait de DeepSeek du catalogue de modèles.
--
-- Le projet n'évalue plus de modèles DeepSeek : l'adaptateur et les entrées
-- du registre ont été retirés de app/llm_client.py, et db/seed.sql ne les
-- insère plus. Cette migration retire les lignes du catalogue sur les bases
-- déjà provisionnées.
--
-- Garde-fou : on ne supprime que les modèles qu'aucun résultat ne référence
-- (la FK results.model_id bloquerait de toute façon) — une base qui aurait de
-- l'historique DeepSeek le conserve, l'entrée du catalogue devenant inerte.

DELETE FROM models m
 WHERE m.provider ILIKE 'deepseek'
   AND NOT EXISTS (SELECT 1 FROM results r WHERE r.model_id = m.id);
