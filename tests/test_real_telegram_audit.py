import inspect

import main
from keyboards import admin_orders_filter_kb


def test_assigned_executor_flow_includes_client_photos():
    source = inspect.getsource(main._render_executor_order)
    assert "get_order_photos" in source
    assert "executor_view_client_photos" in inspect.getsource(main)
    assert "executor_view_result_photos" in inspect.getsource(main)


def test_executor_callbacks_can_recover_from_stale_fsm():
    assert "get_executor(cb.from_user.id)" in inspect.getsource(main.executor_update_status)
    source = inspect.getsource(main.executor_request_photo)
    assert "get_executor(cb.from_user.id)" in source
    assert "state.set_state(ExecutorStates.menu)" in source


def test_admin_filter_exposes_executor_section():
    callbacks = [button.callback_data for row in admin_orders_filter_kb().inline_keyboard for button in row]
    assert "admin:executors" in callbacks
