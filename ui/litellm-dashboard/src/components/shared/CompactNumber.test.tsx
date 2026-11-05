import { render, screen } from "@testing-library/react";
import { expect, it } from "vitest";
import { CompactNumber } from "./CompactNumber";

it.each([[12_300, "12.3K"], [1_250_000, "1.3M"], [1_250_000_000, "1.3B"]] as const)(
  "shows compact %s with its exact value available", (value, display) => {
    const { rerender } = render(<CompactNumber value={value} />);
    const full = value.toLocaleString("en-US");
    expect(screen.getByTitle(full)).toHaveTextContent(display);
    rerender(<CompactNumber value={value} compact={false} />);
    expect(screen.getByTitle(full)).toHaveTextContent(full);
  },
);
