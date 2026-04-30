from typing import Any, overload


def entity[T](cls: type[T]) -> type[T]:
    """Decorator to mark a class as a Styx stateful entity."""
    return cls


def send_async(remote_call: Any) -> None:
    """
    Wrap a remote call to execute asynchronously (fire-and-forget).
    Example: send_async(user.add_money(50))
    """


def get_entity_by_key[T](entity_class: type[T], key: Any) -> T:
    """
    Statically resolve an entity reference by its composite or singular key.
    This call is expanded by the compiler and cannot be executed locally.
    """
    msg = (
        f"Cannot instantiate local reference to remote entity '{entity_class.__name__}'. "
        "Did you forget to run the Styx transpiler?"
    )
    raise NotImplementedError(msg)


def exists(entity: Any) -> bool:
    """
    Check whether the entity at this key has been inserted (its state is non-empty).

    Only meaningful as `exists(self)` inside an @entity method — at runtime the
    Styx framework dispatches to a fresh empty state when a method is called on
    an uninitialized key, so this is the supported way to detect that case.

    Compile-time intrinsic: rewritten to `bool(__state__)`.
    """
    msg = "exists() is a compiler intrinsic and cannot be executed locally. Did you forget to run the Styx compiler?"
    raise NotImplementedError(msg)


# 1. Homogeneous / Dynamic Overload
# Matches: gather(*[item.get_price() for item in cart])
@overload
def gather[T](*args: T) -> tuple[T, ...]: ...


# 2. Heterogeneous / Static Overloads
# Matches: gather(item.get_price())
@overload
def gather[T1](call1: T1) -> tuple[T1]: ...


# Matches: gather(item.get_price(), user.get_profile())
@overload
def gather[T1, T2](call1: T1, call2: T2) -> tuple[T1, T2]: ...


# Matches: gather(call1, call2, call3)
@overload
def gather[T1, T2, T3](call1: T1, call2: T2, call3: T3) -> tuple[T1, T2, T3]: ...


# Matches: gather(call1, call2, call3, call4)
@overload
def gather[T1, T2, T3, T4](call1: T1, call2: T2, call3: T3, call4: T4) -> tuple[T1, T2, T3, T4]: ...


# Matches: gather(call1, call2, call3, call4, call5)
@overload
def gather[T1, T2, T3, T4, T5](call1: T1, call2: T2, call3: T3, call4: T4, call5: T5) -> tuple[T1, T2, T3, T4, T5]: ...


def gather(*args: Any) -> Any:
    """
    Parallelize multiple remote entity calls (Fan-Out/Fan-In).
    This is a Styx compiler intrinsic and will be expanded into a
    distributed barrier step at compile time.
    """
    msg = "gather() is a compiler intrinsic and cannot be executed locally. Did you forget to run the Styx compiler?"
    raise NotImplementedError(msg)
