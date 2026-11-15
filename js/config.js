// Nelyqor — configuration du compte et de la synchronisation (Supabase).
//
// 1. Crée un projet sur https://supabase.com, puis exécute supabase/schema.sql dans « SQL Editor ».
// 2. Copie ici « Project URL » et la clé « anon / publishable » (Project Settings → API).
//    Ces deux valeurs sont PUBLIQUES par conception : la sécurité repose sur les règles RLS
//    de schema.sql. Ne mets JAMAIS ici la clé « service_role » / « secret ».
// 3. Active les fournisseurs Apple, Google et E-mail (Authentication → Providers) et ajoute
//    l'adresse de l'app dans Authentication → URL Configuration (voir README).
//
// Tant que ces valeurs sont vides, l'app fonctionne normalement, uniquement sur l'appareil.

export const SUPABASE_URL = '';
export const SUPABASE_ANON_KEY = '';
