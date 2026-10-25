# IDs de datos sintéticos de Fineract

Actualizados por `python scripts/seed.py`. El proceso es idempotente; las fechas de creación, aprobación, activación y depósito usan 2024-01-01.

| Recurso | ID | Estado / saldo actual |
|---|---:|---|
| Savings product `FPS1` | `1` | Disponible |
| FinPilot Client 1 | `1` | Activo |
| FinPilot Client 2 | `2` | Activo |
| Savings account 1 | `1` | Activa; saldo `1000.0` USD |
| Savings account 2 | `2` | Activa; saldo `1057.0` USD |

Las fechas se envían como `01 January 2024` junto con `dateFormat=`dd MMMM yyyy` y `locale=`en` para evitar diferencias de zona horaria.
