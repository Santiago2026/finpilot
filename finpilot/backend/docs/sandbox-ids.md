# IDs de datos sintéticos de Fineract

Actualizados por `python scripts/seed.py`. El proceso es idempotente; las fechas de creación, aprobación, activación y depósito usan 2024-01-01.

| Recurso | ID | Estado / saldo actual |
|---|---:|---|
| Savings product `FPS1` | `1` | Disponible |
| FinPilot Client 1 | `1` | Activo |
| FinPilot Client 2 | `2` | Activo |
| Savings account 1 | `1` | Activa desde `2024-01-01`; saldo actual `975.0` USD |
| Savings account 2 | `2` | Activa desde `2024-01-01`; saldo actual `1025.0` USD |

El seed dejó inicialmente cada cuenta con `1000.0` USD. Después se aprobó una transferencia HITL de 25 USD de la cuenta 1 a la 2. Las fechas se envían como `01 January 2024` junto con `dateFormat=dd MMMM yyyy` y `locale=en` para evitar diferencias de zona horaria.
