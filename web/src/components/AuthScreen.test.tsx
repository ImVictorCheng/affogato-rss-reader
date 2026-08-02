import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { api } from "../api";
import { AuthScreen } from "./AuthScreen";


it("activates the owner by replacing the one-time password", async () => {
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(
      JSON.stringify({
        setup_required: false,
        activation_required: false,
        authenticated: true,
        onboarding_required: true,
        mode: "owner",
        csrf_token: "csrf-after-activation",
      }),
      { status: 200, headers: { "Content-Type": "application/json" } },
    ),
  );
  vi.stubGlobal("fetch", fetchMock);
  api.setCsrfToken("");
  const onAuthenticated = vi.fn();
  const user = userEvent.setup();

  render(
    <AuthScreen
      status={{
        setup_required: false,
        activation_required: true,
        authenticated: false,
        onboarding_required: true,
        mode: "owner",
      }}
      locale="en"
      onAuthenticated={onAuthenticated}
    />,
  );

  await user.type(screen.getByLabelText("One-time initial password"), "temporary-reader-password");
  await user.type(screen.getByLabelText("New password"), "permanent-reader-password");
  await user.type(screen.getByLabelText("Confirm password"), "permanent-reader-password");
  await user.click(screen.getByRole("button", { name: "Continue" }));

  await waitFor(() => expect(onAuthenticated).toHaveBeenCalledTimes(1));
  expect(String(fetchMock.mock.calls[0][0])).toMatch(/\/api\/v1\/auth\/activate$/);
  expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toEqual({
    initial_password: "temporary-reader-password",
    password: "permanent-reader-password",
  });
  expect(onAuthenticated).toHaveBeenCalledWith(
    expect.objectContaining({ authenticated: true, activation_required: false }),
  );
});
