# STAGE_1 GAP Matrix

| ID | Требование | Production | Telegram Flow | Test | Статус | GAP | Исправление |
|---|---|---|---|---|---|---|---|
| G01 | Клиентский заказ | `start_order` and FSM | callback/message chain | full flow | PASS | - | - |
| G02 | Дата/время | date/time callbacks | state transitions | full flow | PASS | - | - |
| G03 | Метро station | line/station callbacks | station to address | metro regression | PASS | - | - |
| G04 | Телефон, фото, preview | handlers and DB photo rows | client flow | full flow | PASS | - | - |
| G05 | Cancel | `order_cancel_callback`, `cancel_order_handler` | clears FSM/menu | regression | PASS | - | - |
| G06 | My Orders/history/repeat/rating | callbacks and DB filters | navigation | full flow | PASS | - | - |
| G07 | Admin filters/statuses | admin callbacks and SQL filters | admin navigation | integration | PASS | - | - |
| G08 | Executor registration | `add_executor` | registration flow | integration | PASS | - | - |
| G09 | Executor assignment | `assign_executor`, notification | admin to executor | audit regression | PASS | - | send saved photos in `notify_executor` |
| G10 | Executor status/photo callbacks | executor callbacks | stale FSM recovery and photo upload | audit regression | PASS | - | authorize active executor from DB and restore executor state |
| G11 | Client photos admin/executor | `get_order_photos` | detail/assignment | audit regression | PASS | - | - |
| G12 | Result photos client/admin | upload and detail rendering | result delivery | integration | PASS | - | - |
| G13 | LTV reminders | scheduler and callbacks | external Telegram delivery | unit | BLOCKED_EXTERNAL | requires live bot credentials | documented external dependency |
| G14 | Individual cleaning common photo | `individual_cleaning.jpg` | info block | integration | PASS | - | - |
| G15 | Callback answers/error fallback | callbacks use `answer` and Telegram fallback | no spinner | targeted | PASS | - | - |

## Counts

PASS: 15  
PARTIAL: 0  
MISSING: 0  
BROKEN: 0  
BLOCKED_EXTERNAL: 1
