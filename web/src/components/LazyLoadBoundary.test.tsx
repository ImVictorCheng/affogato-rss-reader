import { lazy, Suspense } from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { LazyLoadBoundary } from "./LazyLoadBoundary";

it("keeps the mount root usable and offers reliable recovery after a lazy import rejects", async () => {
  const user = userEvent.setup();
  const onReload = vi.fn();
  const onBack = vi.fn();
  vi.spyOn(console, "error").mockImplementation(() => undefined);
  const FailedWorkspace = lazy(async () => {
    throw new Error("Failed to fetch dynamically imported module");
  });

  const view = render(
    <LazyLoadBoundary
      title="Briefs couldn't load"
      message="The reader is still available."
      reloadLabel="Reload application"
      backLabel="Back to reader"
      onReload={onReload}
      onBack={onBack}
    >
      <Suspense fallback={<p>Loading briefs...</p>}>
        <FailedWorkspace />
      </Suspense>
    </LazyLoadBoundary>,
  );

  expect(await screen.findByRole("alert")).toHaveTextContent("Briefs couldn't load");
  expect(view.container).not.toBeEmptyDOMElement();
  expect(screen.getByRole("button", { name: "Reload application" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Back to reader" })).toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: "Reload application" }));
  await user.click(screen.getByRole("button", { name: "Back to reader" }));

  expect(onReload).toHaveBeenCalledOnce();
  expect(onBack).toHaveBeenCalledOnce();
});
