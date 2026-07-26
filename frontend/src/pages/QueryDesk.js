import React from 'react';
import { HelpCircle } from 'lucide-react';

export function QueryDesk() {
  return (
    <div className="space-y-6" data-testid="query-page">
      {/* Header */}
      <div 
        className="rounded-2xl p-6"
        style={{ backgroundColor: '#34D399' }}
      >
        <div className="flex items-center gap-3">
          <HelpCircle className="h-8 w-8" style={{ color: '#FFFFFF' }} />
          <div>
            <h1 className="text-2xl font-bold" style={{ color: '#FFFFFF' }}>
              Query Desk
            </h1>
            <p style={{ color: '#D1FAE5' }}>
              Submit and track queries
            </p>
          </div>
        </div>
      </div>

      {/* Coming Soon */}
      <div 
        className="rounded-2xl p-12 text-center"
        style={{ backgroundColor: '#A7F3D0' }}
      >
        <HelpCircle className="h-16 w-16 mx-auto mb-4" style={{ color: '#059669' }} />
        <h2 className="text-xl font-semibold mb-2" style={{ color: '#374151' }}>
          Query Desk Coming Soon
        </h2>
        <p style={{ color: '#6B7280' }}>
          Submit and track your queries here.
        </p>
      </div>
    </div>
  );
}
