from app.users import service


def test_create_and_get_user(db_session):
    user = service.create_user(db_session, "alice", ntfy_topic="swing-alerts-alice")
    fetched = service.get_user(db_session, user.id)
    assert fetched is not None
    assert fetched.username == "alice"
    assert fetched.ntfy_topic == "swing-alerts-alice"


def test_get_user_by_username(db_session):
    service.create_user(db_session, "bob")
    assert service.get_user_by_username(db_session, "bob") is not None
    assert service.get_user_by_username(db_session, "nobody") is None


def test_list_users(db_session):
    service.create_user(db_session, "alice")
    service.create_user(db_session, "bob")
    assert {u.username for u in service.list_users(db_session)} == {"alice", "bob"}


def test_add_symbol_uppercases_and_lists_it(db_session):
    user = service.create_user(db_session, "alice")
    service.add_symbol(db_session, user.id, "spy")
    symbols = {s.symbol for s in service.list_symbols(db_session, user.id)}
    assert symbols == {"SPY"}


def test_add_symbol_is_idempotent(db_session):
    user = service.create_user(db_session, "alice")
    service.add_symbol(db_session, user.id, "SPY")
    service.add_symbol(db_session, user.id, "SPY")
    assert len(service.list_symbols(db_session, user.id)) == 1


def test_remove_symbol_deactivates_not_deletes(db_session):
    user = service.create_user(db_session, "alice")
    service.add_symbol(db_session, user.id, "SPY")

    assert service.remove_symbol(db_session, user.id, "SPY") is True
    assert service.list_symbols(db_session, user.id) == []
    assert service.remove_symbol(db_session, user.id, "SPY") is False  # already inactive


def test_add_symbol_after_removal_reactivates(db_session):
    user = service.create_user(db_session, "alice")
    service.add_symbol(db_session, user.id, "SPY")
    service.remove_symbol(db_session, user.id, "SPY")
    service.add_symbol(db_session, user.id, "SPY")

    assert {s.symbol for s in service.list_symbols(db_session, user.id)} == {"SPY"}


def test_all_active_symbols_across_users(db_session):
    alice = service.create_user(db_session, "alice")
    bob = service.create_user(db_session, "bob")
    service.add_symbol(db_session, alice.id, "SPY")
    service.add_symbol(db_session, bob.id, "QQQ")

    assert service.all_active_symbols(db_session) == {"SPY", "QQQ"}


def test_users_subscribed_to_symbol(db_session):
    alice = service.create_user(db_session, "alice")
    bob = service.create_user(db_session, "bob")
    service.add_symbol(db_session, alice.id, "SPY")
    service.add_symbol(db_session, bob.id, "QQQ")

    subscribed = service.users_subscribed_to(db_session, "SPY")
    assert [u.username for u in subscribed] == ["alice"]


def test_users_subscribed_to_excludes_removed_symbols(db_session):
    alice = service.create_user(db_session, "alice")
    service.add_symbol(db_session, alice.id, "SPY")
    service.remove_symbol(db_session, alice.id, "SPY")

    assert service.users_subscribed_to(db_session, "SPY") == []
