import { describe, expect, it } from "vitest";
import {
  composeSiteIdentity,
  customizeSiteIdentity,
  DOMAIN_TEMPLATES,
  supportsGeneratedIdentity,
} from "./domainThemes";

describe("site identities", () => {
  it("gives every built-in field a distinct name and generated logo", () => {
    expect(DOMAIN_TEMPLATES).toHaveLength(12);
    const identities = DOMAIN_TEMPLATES.map((item) => composeSiteIdentity([item.name.en], "en"));

    expect(new Set(identities.map((identity) => identity.name)).size).toBe(12);
    identities.forEach((identity, index) => {
      expect(identity.logo_kind).toBe("generated");
      expect(identity.primary_template).toBe(DOMAIN_TEMPLATES[index].id);
      expect(identity.secondary_template).toBeUndefined();
    });
  });

  it("gives all 66 built-in field pairs a distinct name and combined logo", () => {
    const identities = [];
    for (let first = 0; first < DOMAIN_TEMPLATES.length; first += 1) {
      for (let second = first + 1; second < DOMAIN_TEMPLATES.length; second += 1) {
        identities.push(composeSiteIdentity([
          DOMAIN_TEMPLATES[first].name.en,
          DOMAIN_TEMPLATES[second].name.en,
        ], "en"));
      }
    }

    expect(identities).toHaveLength(66);
    expect(new Set(identities.map((identity) => identity.name)).size).toBe(66);
    identities.forEach((identity) => {
      expect(identity.logo_kind).toBe("generated");
      expect(identity.primary_template).toBeTruthy();
      expect(identity.secondary_template).toBeTruthy();
    });
  });

  it("falls back to the default brand for custom or multi-field collections", () => {
    const threeFields = DOMAIN_TEMPLATES.slice(0, 3).map((item) => item.name.en);

    expect(supportsGeneratedIdentity(["Quantum computing"])).toBe(false);
    expect(supportsGeneratedIdentity(threeFields)).toBe(false);
    expect(composeSiteIdentity(["Quantum computing"], "en")).toEqual({
      name: "Affogato RSS Reader",
      source: "default",
      logo_kind: "default",
    });
    expect(composeSiteIdentity(threeFields, "en")).toEqual({
      name: "Affogato RSS Reader",
      source: "default",
      logo_kind: "default",
    });
  });

  it("uses a custom name and safe uploaded-image identity when supplied", () => {
    const logo = "data:image/png;base64,iVBORw0KGgo=";
    expect(composeSiteIdentity(["Quantum computing"], "en", "Qubit Observer", logo)).toEqual({
      name: "Qubit Observer",
      source: "custom",
      logo_kind: "upload",
      logo_data_url: logo,
    });
  });

  it("can override a template name while retaining its generated logo", () => {
    const fallback = composeSiteIdentity(["Quantum physics"], "en");
    const customized = customizeSiteIdentity(fallback, "Qubit Observer");

    expect(customized).toEqual({
      name: "Qubit Observer",
      source: "custom",
      logo_kind: "generated",
      primary_template: "quantum-physics",
      secondary_template: undefined,
    });
    expect(customizeSiteIdentity(fallback, fallback.name)).toBe(fallback);
  });

  it("can override only the logo while retaining the template name", () => {
    const fallback = composeSiteIdentity(["Quantum physics", "Artificial intelligence"], "en");
    const logo = "data:image/webp;base64,UklGRg==";

    expect(customizeSiteIdentity(fallback, "", logo)).toEqual({
      name: fallback.name,
      source: "custom",
      logo_kind: "upload",
      logo_data_url: logo,
    });
  });
});
