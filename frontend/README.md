<div align="center">
<img width="1200" height="475" alt="GHBanner" src="https://github.com/user-attachments/assets/0aa67016-6eaf-458a-adb2-6e31a0763ed6" />
</div>

# Run and deploy your AI Studio app

This contains everything you need to run your app locally.

View your app in AI Studio: https://ai.studio/apps/drive/1xAXO18YijIeSTdjjBV6NT0n6pX77HOOh

## Run Locally

**Prerequisites:**  Node.js


1. Install dependencies:
   `npm install`
2. For static-only local UI work, set the frontend `.env.local` values you need and run `npm run dev`
3. For the full production-style stack with Vercel API routes:
   - keep client-side `VITE_*` values in `frontend/.env.local`
   - put server-only `SUPABASE_URL` and `SUPABASE_SECRET_KEY` (or `SUPABASE_SERVICE_ROLE_KEY`) in the repository root `.env.local`, or add them to the Vercel project's Development environment
4. `vercel dev` expects Vite to bind to `process.env.PORT`, which this project now supports
5. If your Vercel Project Root Directory is set to `frontend`, run `vercel dev` from the repository root instead of from inside `frontend`
6. Run the app:
   `npm run dev`
