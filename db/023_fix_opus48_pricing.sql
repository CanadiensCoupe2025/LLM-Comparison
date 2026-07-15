-- -------------------------------------------------------------
-- Migration 023 — correction du tarif de claude-opus-4-8.
--
-- Le seed l'avait à 15$/75$ par MTok (tarif Opus 4.1/4.5 historique) ;
-- le tarif réel d'Opus 4.8 est 5$ input / 25$ output par MTok
-- (https://www.anthropic.com/pricing). Comme results.cost et les USD
-- des dashboards sont dérivés de ces colonnes, l'ancien tarif gonflait
-- le coût rapporté d'Opus ×3. seed.sql est corrigé en parallèle pour
-- les bases fraîches ; cette migration corrige les bases DÉJÀ
-- provisionnées (les results.cost historiques, figés à l'insertion,
-- ne sont pas retouchés — seuls les runs futurs sont affectés).
-- -------------------------------------------------------------
UPDATE models
   SET input_cost  = 0.0000050,   -- 5 $ / MTok
       output_cost = 0.0000250    -- 25 $ / MTok
 WHERE provider = 'Anthropic'
   AND name = 'claude-opus-4-8';
