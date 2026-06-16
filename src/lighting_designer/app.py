# -*- coding: utf-8 -*-
"""Application entry point.

This is the thin Qt bootstrap layer. The full implementation lives in
``lighting_designer._core`` and is exposed through the clean sub-package API
(``lighting_designer.models``, ``.engines``, ``.data``, ``.photometry``,
``.io``, ``.ui``, ``.services``). Keeping ``main`` here preserves the public
entry points: ``from lighting_designer import main`` and the ``ldesign``
console script.
"""
from __future__ import annotations

from lighting_designer._core import main

__all__ = ["main"]


if __name__ == "__main__":
    main()
