# Budget Tracker (Cualto)

Parser de notificaciones bancarias por correo → presupuesto mensual, con alertas
por Telegram. Dos piezas conviven: el pipeline v1 por cron (`legacy/`) y la app
web multiusuario v2 (`web/`, FastAPI + Postgres) desplegada en cualtoapp.com.

## Documentación
- `README.md` — qué es y cómo empezar
- `DEPLOYMENT.md` — despliegue, incluido el paso a internet
- `BRAND.md` / `BRANDING-CONTEXT.md` — identidad visual
- `core/budgetcore/` — parsers, categorización, dedupe y mensajes (lógica pura,
  independiente de v1/v2)

## Cómo se ejecuta
```bash
pip install -r requirements.txt
pytest                                   # la suite completa
cp config.example.toml config.toml       # credenciales reales, git-ignored
```
Detalles de despliegue y del stack de Docker, en `DEPLOYMENT.md`.

## Seguimiento del proyecto
El código vive aquí; el seguimiento (estado, decisiones y sus porqués, backlog
de producto y de comercialización) se lleva aparte, en un vault personal de
notas, bajo `20_Projects/budget-tracker/`. Si trabajas en esta máquina, ese vault
está declarado en `.claude/settings.local.json` — que no se versiona — así que la
sesión puede leerlo sin configurar nada.

Regla de reparto: el código y cómo ejecutarlo, aquí. Qué se decidió y por qué,
allí. Nada de duplicar los archivos de seguimiento dentro de este repo.
