"""Model-based falsification with separate search and confirmation seeds."""

__all__ = ["Config", "ExperimentError", "run_experiment"]


def __getattr__(name):
    # Formal-only clients must not import numerical dependencies on startup.
    if name in __all__:
        from . import experiment

        return getattr(experiment, name)
    raise AttributeError(name)
