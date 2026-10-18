"use client";

import { createContext, useContext } from "react";

const DashboardReadinessContext = createContext(true);

export const DashboardReadinessProvider = DashboardReadinessContext.Provider;

export const useDashboardReadiness = () => useContext(DashboardReadinessContext);
