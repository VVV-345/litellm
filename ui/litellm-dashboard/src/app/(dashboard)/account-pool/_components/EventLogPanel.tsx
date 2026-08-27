"use client";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { accountPoolKeys, getEvents } from "../api";
import type { EventLogEntry, EventLogFilters } from "../types";
import { EventDetailsDialog, EventFilters, EventTable } from "./EventLogViews";

const eventPageSize = 10;
const emptyFilters: EventLogFilters = { limit: eventPageSize };

const initialFiltersFor = (channelId: string | null): EventLogFilters =>
  channelId ? { ...emptyFilters, channel_id: channelId } : emptyFilters;

const cleanFilters = (filters: EventLogFilters): EventLogFilters =>
  Object.fromEntries(
    Object.entries(filters)
      .filter(([, value]) => value !== "" && value !== undefined)
      .map(([key, value]) => [
        key,
        (key === "occurred_after" || key === "occurred_before") && typeof value === "string"
          ? new Date(value).toISOString()
          : value,
      ]),
  );

interface EventLogPanelProps {
  accessToken: string;
  initialChannelId?: string | null;
}

export default function EventLogPanel({ accessToken, initialChannelId = null }: EventLogPanelProps) {
  const initialFilters = initialFiltersFor(initialChannelId);
  const [draftFilters, setDraftFilters] = useState<EventLogFilters>(initialFilters);
  const [activeFilters, setActiveFilters] = useState<EventLogFilters>(initialFilters);
  const [cursorHistory, setCursorHistory] = useState<string[]>([]);
  const [selectedEvent, setSelectedEvent] = useState<EventLogEntry | null>(null);
  const queryFilters = cleanFilters(activeFilters);
  const cursor = cursorHistory.at(-1);
  const requestFilters = cursor === undefined ? queryFilters : { ...queryFilters, cursor };
  const eventsQuery = useQuery({
    queryKey: accountPoolKeys.events(requestFilters),
    queryFn: () => getEvents(accessToken, requestFilters),
  });
  const events = eventsQuery.data?.events ?? [];

  const applyFilters = () => {
    setCursorHistory([]);
    setActiveFilters(cleanFilters({ ...draftFilters, limit: eventPageSize }));
  };

  const resetFilters = () => {
    setCursorHistory([]);
    setDraftFilters(emptyFilters);
    setActiveFilters(emptyFilters);
  };

  const nextPage = () => {
    const nextCursor = eventsQuery.data?.next_cursor;
    if (nextCursor) setCursorHistory((history) => [...history, nextCursor]);
  };

  const previousPage = () => setCursorHistory((history) => history.slice(0, -1));

  return (
    <div className="min-w-0 space-y-4">
      <EventFilters
        draftFilters={draftFilters}
        isFetching={eventsQuery.isFetching}
        onChange={setDraftFilters}
        onApply={applyFilters}
        onReset={resetFilters}
      />
      <EventTable
        events={events}
        isError={eventsQuery.isError}
        isFetching={eventsQuery.isFetching}
        isLoading={eventsQuery.isLoading}
        hasNextPage={Boolean(eventsQuery.data?.next_cursor)}
        hasPreviousPage={cursorHistory.length > 0}
        isPreviousPageEnabled={!eventsQuery.isFetching}
        onNextPage={nextPage}
        onPreviousPage={previousPage}
        pageNumber={cursorHistory.length + 1}
        pageSize={eventPageSize}
        onSelect={setSelectedEvent}
      />
      <EventDetailsDialog event={selectedEvent} onClose={() => setSelectedEvent(null)} />
    </div>
  );
}
