/**
 * ASSETS_BASE: base URL for static assets (logos, headshots).
 *
 * - Local dev (VITE_USE_DB=false): '' → falls through to VITE_API_BASE_URL
 * - Production (Vercel): '' → relative paths like /assets/team_logos/...
 *   (Vite serves frontend/public/ as root, so /assets/... works)
 *
 * This replaces all `${import.meta.env.VITE_API_BASE_URL}/assets/...`
 * references throughout the frontend components.
 */
export const ASSETS_BASE = import.meta.env.VITE_ASSETS_URL ?? '';
