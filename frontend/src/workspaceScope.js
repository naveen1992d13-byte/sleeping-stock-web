import React, { createContext, useContext } from 'react';
import { useOutletContext } from 'react-router-dom';

/**
 * Header Brand/Dealer/Branch scope for workspace modules.
 * Prefer this over useOutletContext so keep-alive tabs (hidden, not routed
 * through <Outlet />) still receive the live scope.
 */
export const WorkspaceScopeContext = createContext(null);

export function WorkspaceScopeProvider({ value, children }) {
  return (
    <WorkspaceScopeContext.Provider value={value}>
      {children}
    </WorkspaceScopeContext.Provider>
  );
}

export function useWorkspaceScope() {
  const fromProvider = useContext(WorkspaceScopeContext);
  const fromOutlet = useOutletContext();
  return fromProvider || fromOutlet || {};
}

/** Stable key for remounting keep-alive modules on scope or session change. */
export function buildScopeKey({ scopeBrand, scopeDealer, scopeBranch, userId, sessionEpoch }) {
  return [
    String(scopeBrand ?? ''),
    String(scopeDealer ?? ''),
    String(scopeBranch ?? ''),
    String(userId ?? ''),
    String(sessionEpoch ?? '0'),
  ].join('|');
}
