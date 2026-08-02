import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { Brand, ComboBox, SelectMenu } from "./Common";

describe("Brand", () => {
  it("keeps a custom site name when the user falls back to the default logo", () => {
    render(<Brand identity={{
      name: "Qubit Observer",
      source: "custom",
      logo_kind: "default",
    }} />);

    expect(screen.getByText("Qubit Observer")).toBeInTheDocument();
    expect(screen.queryByText("RSS")).not.toBeInTheDocument();
  });
});

describe("Dropdown controls", () => {
  it("uses the same styled menu for editable and fixed dropdowns", async () => {
    const user = userEvent.setup();
    const onFolder = vi.fn();
    const onInterval = vi.fn();
    render(<>
      <ComboBox value="" onChange={onFolder} label="Folder" options={[
        { value: "", label: "Uncategorized" },
        { value: "Lab", label: "Lab", meta: "1 feed" },
      ]} />
      <SelectMenu value="45" onChange={onInterval} label="Interval" options={[
        { value: "45", label: "45 min" },
        { value: "60", label: "60 min" },
      ]} />
    </>);

    await user.click(screen.getByRole("button", { name: "Show Folder options" }));
    expect(screen.getByRole("listbox", { name: "Folder" })).toHaveClass("app-dropdown__menu");
    await user.click(screen.getByRole("option", { name: /Lab/ }));
    expect(onFolder).toHaveBeenCalledWith("Lab");

    await user.click(screen.getByRole("combobox", { name: "Interval" }));
    expect(screen.getByRole("listbox", { name: "Interval" })).toHaveClass("app-dropdown__menu");
    await user.click(screen.getByRole("option", { name: /60 min/ }));
    expect(onInterval).toHaveBeenCalledWith("60");
  });
});
