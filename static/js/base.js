/*
 * Site-wide client behavior.
 *
 * Content arrives both with the page and in HTMX swaps, so nothing here is
 * attached to individual elements. Listeners are delegated from the document,
 * and anything that needs setting up per element is handled in the single
 * htmx.onLoad callback (and torn down again in htmx:beforeCleanupElement).
 * Templates rendered into the page by HTMX must not carry scripts of their own.
 */
(function () {
  "use strict";

  const TOOLTIP_SELECTOR = '[data-bs-toggle="tooltip"]';
  const MODAL_ID = "characterModal";
  const MODAL_CONTENT_ID = "characterModalContent";
  const MODAL_PLACEHOLDER_ID = "characterModalPlaceholder";
  const MODAL_OPENER_SELECTOR = "[data-character-modal-open]";
  const OTHER_OPTION = "__other__";
  // Comfortably longer than any of Bootstrap's transitions.
  const TRANSITION_SETTLE_MS = 1000;

  /** Elements matching selector within root, including root itself. */
  function findAll(root, selector) {
    const found = Array.from(root.querySelectorAll(selector));
    if (root.matches && root.matches(selector)) {
      found.unshift(root);
    }
    return found;
  }

  // ---------------------------------------------------------------- Tooltips

  function initTooltips(root) {
    findAll(root, TOOLTIP_SELECTOR).forEach(function (el) {
      // Tooltips only follow the pointer. Showing them on focus too leaves
      // them stuck open on links that have just opened the modal.
      // Without animation there is never a fade in progress, so a tooltip
      // can be disposed of at any moment (see disposeComponents).
      bootstrap.Tooltip.getOrCreateInstance(el, {
        trigger: "hover",
        container: "body",
        animation: false,
      });
    });
  }

  function hideTooltips() {
    // Bootstrap marks the trigger of a visible tooltip with aria-describedby.
    document
      .querySelectorAll(TOOLTIP_SELECTOR + "[aria-describedby]")
      .forEach(function (el) {
        const tooltip = bootstrap.Tooltip.getInstance(el);
        if (tooltip) {
          tooltip.hide();
        }
      });
  }

  /**
   * Release whatever Bootstrap has attached to an element that is going away.
   * Bootstrap holds on to the elements of its components, and a tooltip that
   * outlives its trigger is left on screen with nothing to dismiss it.
   *
   * Tooltips must go while the element is still in the document, because
   * they also listen to the modal that contains them.
   */
  function disposeComponents(el) {
    [bootstrap.Tooltip, bootstrap.Dropdown].forEach(function (Component) {
      const instance = Component.getInstance(el);
      if (instance) {
        instance.dispose();
      }
    });
    const collapse = bootstrap.Collapse.getInstance(el);
    if (collapse) {
      // Bootstrap still has work to do on the element when an animation ends.
      const delay = el.classList.contains("collapsing") ? TRANSITION_SETTLE_MS : 0;
      setTimeout(function () {
        collapse.dispose();
      }, delay);
    }
    const modal = bootstrap.Modal.getInstance(el);
    if (modal && modalPhase(el).phase === "hidden") {
      modal.dispose();
      modalPhases.delete(el);
    }
  }

  document.addEventListener("click", hideTooltips);
  document.addEventListener("show.bs.modal", hideTooltips);

  // --------------------------------------------------------- Character modal
  //
  // One modal (see character_detail.html) shows whatever is loaded into
  // #characterModalContent. It opens when a request for that content starts,
  // and closes when the server says there is nothing more to do.
  //
  // Bootstrap ignores show() and hide() while a modal is animating, so the
  // phase of each modal is tracked, and a request that arrives mid-animation
  // is carried out when the animation finishes.

  const modalPhases = new WeakMap(); // element -> {phase, wanted}

  function modalPhase(el) {
    if (!modalPhases.has(el)) {
      modalPhases.set(el, {
        phase: el.classList.contains("show") ? "shown" : "hidden",
        wanted: null,
      });
    }
    return modalPhases.get(el);
  }

  function isModalOpen(el) {
    const phase = modalPhase(el).phase;
    return phase === "shown" || phase === "showing";
  }

  function openModal(el) {
    const state = modalPhase(el);
    if (state.phase === "hiding") {
      state.wanted = "open";
    } else {
      state.wanted = null;
      bootstrap.Modal.getOrCreateInstance(el).show();
    }
  }

  function closeModal(el) {
    const state = modalPhase(el);
    if (state.phase === "showing") {
      state.wanted = "closed";
    } else {
      state.wanted = null;
      bootstrap.Modal.getOrCreateInstance(el).hide();
    }
  }

  [
    ["show.bs.modal", "showing"],
    ["shown.bs.modal", "shown"],
    ["hide.bs.modal", "hiding"],
    ["hidden.bs.modal", "hidden"],
  ].forEach(function (pair) {
    document.addEventListener(pair[0], function (evt) {
      if (evt.defaultPrevented) {
        return;
      }
      const state = modalPhase(evt.target);
      state.phase = pair[1];
      const wanted = state.wanted;
      if (state.phase === "shown" && wanted === "closed") {
        closeModal(evt.target);
      } else if (state.phase === "hidden" && wanted === "open") {
        openModal(evt.target);
      }
    });
  });

  /** Put the loading placeholder into the modal, discarding what was there. */
  function resetModalContent(content) {
    const placeholder = document.getElementById(MODAL_PLACEHOLDER_ID);
    findAll(content, "*").forEach(disposeComponents);
    content.replaceChildren(
      placeholder ? placeholder.content.cloneNode(true) : ""
    );
  }

  function isModalContent(el) {
    return Boolean(el) && el.id === MODAL_CONTENT_ID;
  }

  // Open the modal as soon as something is requested for it, so that there is
  // immediate feedback. What it last showed is stale by now, so start over.
  document.addEventListener("htmx:beforeRequest", function (evt) {
    const modal = document.getElementById(MODAL_ID);
    if (!modal || !isModalContent(evt.detail.target)) {
      return;
    }
    if (!isModalOpen(modal)) {
      resetModalContent(evt.detail.target);
    }
    openModal(modal);
  });

  // If the first thing requested for the modal never arrives, there is
  // nothing to show in it. (The error itself is reported below.)
  document.addEventListener("htmx:afterRequest", function (evt) {
    const modal = document.getElementById(MODAL_ID);
    const content = evt.detail.target;
    if (
      modal &&
      isModalContent(content) &&
      !evt.detail.successful &&
      content.querySelector("[data-character-modal-placeholder]")
    ) {
      closeModal(modal);
    }
  });

  // Swapping the content removes whatever had focus. Bring focus back into
  // the modal, or the keyboard (Escape, Tab) stops working on it.
  document.addEventListener("htmx:afterSettle", function (evt) {
    const modal = document.getElementById(MODAL_ID);
    if (
      modal &&
      isModalContent(evt.detail.target) &&
      isModalOpen(modal) &&
      !modal.contains(document.activeElement)
    ) {
      modal.focus({ preventScroll: true });
    }
  });

  // Sent by the server (as an HX-Trigger header) when an action is complete.
  document.addEventListener("close-character-modal", function () {
    document.querySelectorAll(".modal").forEach(function (el) {
      if (isModalOpen(el)) {
        closeModal(el);
      }
    });
  });

  // Links that open the modal are also ordinary links to a page with the same
  // content. Let the browser follow them when the modal can't be used: if this
  // page doesn't have one, or the click asks for a new tab or window.
  document.addEventListener(
    "click",
    function (evt) {
      if (!(evt.target instanceof Element)) {
        return;
      }
      const opener = evt.target.closest("a[href]" + MODAL_OPENER_SELECTOR);
      if (!opener) {
        return;
      }
      const modified =
        evt.button !== 0 ||
        evt.ctrlKey ||
        evt.metaKey ||
        evt.shiftKey ||
        evt.altKey;
      if (modified || !document.getElementById(MODAL_ID)) {
        // Keeps the click from reaching HTMX, without cancelling navigation.
        evt.stopPropagation();
      }
    },
    true
  );

  // When a boosted navigation replaces the page while a modal is open, the
  // modal and its backdrop go with it, but not what Bootstrap did to <body>.
  document.addEventListener("htmx:beforeHistorySave", function () {
    document.querySelectorAll(".modal.show").forEach(function (el) {
      const modal = bootstrap.Modal.getInstance(el);
      if (modal) {
        modal.dispose();
      }
      el.classList.remove("show");
      el.style.display = "none";
      modalPhases.delete(el);
    });
    document.querySelectorAll(".modal-backdrop").forEach(function (el) {
      el.remove();
    });
    document.body.classList.remove("modal-open");
    document.body.style.removeProperty("overflow");
    document.body.style.removeProperty("padding-right");
  });

  // ----------------------------------------------------- Feature option form
  //
  // Features that take an option may offer both a list of options and a
  // freeform "Other". The custom text only applies when Other is selected.

  function syncFreeformOption(form) {
    const freeform = form.querySelector('[name="option_freeform"]');
    const other = form.querySelector(
      'input[name="option"][value="' + OTHER_OPTION + '"]'
    );
    if (!freeform || !other) {
      return;
    }
    freeform.disabled = !other.checked;
    freeform.required = other.checked;
    if (!other.checked) {
      freeform.value = "";
    }
  }

  document.addEventListener("change", function (evt) {
    if (evt.target instanceof Element && evt.target.matches('input[name="option"]')) {
      if (evt.target.form) {
        syncFreeformOption(evt.target.form);
      }
    }
  });

  // ------------------------------------------------------------ HTMX lifecycle

  htmx.onLoad(function (content) {
    initTooltips(content);
    findAll(content, '[name="option_freeform"]').forEach(function (el) {
      if (el.form) {
        syncFreeformOption(el.form);
      }
    });
  });

  document.addEventListener("htmx:beforeCleanupElement", function (evt) {
    disposeComponents(evt.target);
  });

  document.addEventListener("htmx:afterRequest", function (evt) {
    // Adapted from https://xvello.net/blog/htmx-error-handling/
    const errorTarget = document.getElementById("htmx-alert");
    if (!errorTarget) {
      return;
    }
    if (evt.detail.successful) {
      // Successful request, clear out alert
      errorTarget.setAttribute("hidden", "true");
      errorTarget.innerText = "";
    } else if (evt.detail.failed && evt.detail.xhr) {
      // Server error with response contents, equivalent to htmx:responseError
      console.warn("Server error", evt.detail);
      const xhr = evt.detail.xhr;
      errorTarget.innerText = `Unexpected server error: ${xhr.status} - ${xhr.statusText}`;
      errorTarget.removeAttribute("hidden");
    } else if (!evt.detail.xhr || evt.detail.xhr.status === 0) {
      // Unspecified failure, usually caused by network error
      console.error("Unexpected htmx error", evt.detail);
    }
    // Otherwise the response was a client-side redirect or refresh.
  });
})();
