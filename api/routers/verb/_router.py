# api/routers/verb/_router.py
#
# Shared APIRouter for the verb package. Kept in its own module so route
# submodules can register their handlers against the *same* router without
# import cycles. (Original: api/routers/verb.py defined `router = APIRouter()`.)

from fastapi import APIRouter

router = APIRouter()
