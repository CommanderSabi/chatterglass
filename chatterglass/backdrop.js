// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Commander Sabi
(function () {
  // A backdrop of our own, injected behind the page's content.
  //
  // Third-party chat pages paint their elements (message bubbles) but usually no
  // usable background, so fading the window would fade the text too. This puts a
  // fixed, pointer-transparent panel underneath everything, and only its alpha
  // is driven by the opacity slider. Pages that do paint their own background
  // simply cover it, which is why this can be injected into any chat overlay.
  if (window.__cgBackdropLoaded__) { return; }
  window.__cgBackdropLoaded__ = true;

  var node = null;

  function ensure() {
    if (node && node.parentNode) { return node; }
    node = document.createElement("div");
    node.id = "__cg_backdrop";
    node.style.position = "fixed";
    node.style.left = "0";
    node.style.top = "0";
    node.style.right = "0";
    node.style.bottom = "0";
    node.style.pointerEvents = "none";
    node.style.zIndex = "-2147483647";
    node.style.borderRadius = "10px";
    node.style.background = "rgba(18, 19, 24, 0.72)";
    (document.body || document.documentElement).appendChild(node);
    return node;
  }

  window.__cgSetBackdrop = function (alpha) {
    var panel = ensure();
    var value = (typeof alpha === "number") ? alpha : 0.72;
    value = Math.max(0, Math.min(1, value));
    panel.style.background = "rgba(18, 19, 24, " + value + ")";
  };
})();
