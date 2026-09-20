"""Reuse one immutable architecture snapshot for the four affected route assertions."""
from tests.architecture import test_portfolio_public_owners as routes

rows = routes._portfolio_rows()
routes._portfolio_rows = lambda: rows
for check in (
    routes.test_architecture_portfolio_public_routes_preserve_exact_owner_identity,
    routes.test_architecture_portfolio_importers_keep_exact_local_legacy_bindings,
    routes.test_architecture_portfolio_current_private_edges_are_closed,
    routes.test_architecture_portfolio_current_private_import_contract_is_closed,
):
    check()
    print(check.__name__, 'PASS', flush=True)
