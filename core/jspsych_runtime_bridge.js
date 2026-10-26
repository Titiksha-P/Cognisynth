/**
 * Cognisynth DOM adapter prototype.
 * Only pre-registered elements may be mutated; never eval model-authored code.
 */
"use strict";

function applyBlueprint(blueprint, registry, documentRef = document) {
  if (!blueprint || !registry || typeof registry !== "object") {
    throw new TypeError("Blueprint and registry are required");
  }
  const critical = new Set(["master_emergency_shutoff", "alarm_acknowledge", "manual_override"]);
  const visible = new Set(blueprint.visible_controls || []);
  for (const id of critical) {
    if (!visible.has(id)) throw new Error("Refusing blueprint that hides critical control: " + id);
  }

  const changed = [];
  for (const id of visible) {
    const elementId = registry[id];
    if (typeof elementId !== "string") throw new Error("Unregistered control: " + id);
    const element = documentRef.getElementById(elementId);
    if (!element) throw new Error("Missing registered DOM element: " + elementId);
    element.hidden = false;
    element.setAttribute("aria-hidden", "false");
    changed.push(id);
  }

  for (const id of (blueprint.collapsed_streams || [])) {
    const elementId = registry[id];
    if (typeof elementId !== "string") throw new Error("Unregistered stream: " + id);
    const element = documentRef.getElementById(elementId);
    if (!element) throw new Error("Missing registered DOM element: " + elementId);
    element.hidden = true;
    element.setAttribute("aria-hidden", "true");
  }
  return { applied: true, visibleControls: changed };
}

if (typeof module !== "undefined" && module.exports) module.exports = { applyBlueprint };
