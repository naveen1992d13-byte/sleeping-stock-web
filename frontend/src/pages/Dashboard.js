import React, { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Users,
  Building2,
  Truck,
  ClipboardList,
  Upload,
  Search,
  BarChart3,
  IndianRupee,
  Activity,
} from "lucide-react";
import { Button } from "../components/ui/button";
import { useAuth } from "../App";

function CountUp({ value }) {
  const [count, setCount] = useState(0);

  useEffect(() => {
    const num = Number(String(value).replace(/[^0-9]/g, ""));
    if (!num) return;

    let start = 0;
    const timer = setInterval(() => {
      start += Math.ceil(num / 40);
      if (start >= num) {
        start = num;
        clearInterval(timer);
      }
      setCount(start);
    }, 25);

    return () => clearInterval(timer);
  }, [value]);

  return <>{count.toLocaleString("en-IN")}</>;
}

export default function Dashboard() {
  const navigate = useNavigate();
  const { user } = useAuth();

  const isMasterAdmin = user?.role === "master";

  const [dealer, setDealer] = useState("All Dealers");
  const [brand, setBrand] = useState("All Brands");
  const [branch, setBranch] = useState("All Branches");

  const data = {
    registeredDealers: 248,
    branchesConnected: 684,
    nonMovingValue: "₹42.80 Cr",
    movedValue: "₹18.25 Cr",
    pendingRequests: 126,
    onlineDealers: 42,
  };

  const cards = [
    {
      title: "Registered Dealers",
      value: data.registeredDealers,
      icon: Users,
      type: "number",
    },
    {
      title: "Branches Connected",
      value: data.branchesConnected,
      icon: Building2,
      type: "number",
    },
    {
      title: "Non-Moving Value",
      value: data.nonMovingValue,
      icon: IndianRupee,
      type: "text",
      color: "#EA580C",
    },
    {
      title: "Moved Value",
      value: data.movedValue,
      icon: Truck,
      type: "text",
      color: "#22C55E",
    },
  ];

  return (
    <div className="min-h-screen p-4 md:p-6" style={{ backgroundColor: "#D1FAE5" }}>
      <div className="rounded-2xl p-5 mb-5 shadow-sm" style={{ backgroundColor: "#FFFFFF", border: "1px solid #D9DED3" }}>
        <div className="flex flex-col xl:flex-row xl:items-center xl:justify-between gap-4">
          <div>
            <h1 className="text-2xl md:text-3xl font-bold" style={{ color: "#263326" }}>
              Sleeping Stock Dashboard
            </h1>
            <p className="text-sm mt-1" style={{ color: "#5F6B5A" }}>
              Dealer network, branch count, non-moving value and moved value summary.
            </p>
          </div>

          {isMasterAdmin ? (
            <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
              <select value={dealer} onChange={(e) => setDealer(e.target.value)} className="px-4 py-2 rounded-xl border">
                <option>All Dealers</option>
                <option>FPL Hyundai</option>
                <option>Kun Hyundai</option>
                <option>MPL Hyundai</option>
              </select>

              <select value={brand} onChange={(e) => setBrand(e.target.value)} className="px-4 py-2 rounded-xl border">
                <option>All Brands</option>
                <option>Hyundai</option>
                <option>Kia</option>
                <option>Toyota</option>
              </select>

              <select value={branch} onChange={(e) => setBranch(e.target.value)} className="px-4 py-2 rounded-xl border">
                <option>All Branches</option>
                <option>Chrompet</option>
                <option>Red Hills</option>
                <option>Koyambedu</option>
                <option>Guduvanchery</option>
              </select>
            </div>
          ) : (
            <div className="rounded-xl px-4 py-3" style={{ backgroundColor: "#E8F5E9" }}>
              <p className="text-sm font-semibold">Showing assigned data</p>
              <p className="text-xs mt-1">
                {user?.brand || "Brand"} / {user?.group || "Dealer"} / {user?.location || "Branch"}
              </p>
            </div>
          )}
        </div>
      </div>

      {isMasterAdmin && (
        <div className="mb-5 rounded-xl p-3 text-sm" style={{ backgroundColor: "#F8FAF7", border: "1px solid #D9DED3" }}>
          Filter Applied: <b>{dealer}</b> / <b>{brand}</b> / <b>{branch}</b>
        </div>
      )}

      <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4 mb-5">
        {cards.map((card, index) => {
          const Icon = card.icon;
          return (
            <div key={index} className="rounded-2xl p-5 shadow-sm hover:shadow-md transition" style={{ backgroundColor: "#FFFFFF", border: "1px solid #D9DED3" }}>
              <div className="flex justify-between items-start">
                <div>
                  <p className="text-sm" style={{ color: "#6B7280" }}>{card.title}</p>
                  <h2 className="text-2xl md:text-3xl font-bold mt-2" style={{ color: card.color || "#263326" }}>
                    {card.type === "number" ? <CountUp value={card.value} /> : card.value}
                  </h2>
                </div>
                <div className="h-12 w-12 rounded-xl flex items-center justify-center" style={{ backgroundColor: "#E8F5E9" }}>
                  <Icon className="h-6 w-6" style={{ color: card.color || "#22C55E" }} />
                </div>
              </div>
            </div>
          );
        })}
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-5 mb-5">
        <div className="rounded-2xl p-5 shadow-sm" style={{ backgroundColor: "#FFFFFF", border: "1px solid #D9DED3" }}>
          <h2 className="text-lg font-bold mb-4">Movement Progress</h2>

          {[
            { label: "Stock Movement", value: 68 },
            { label: "Dealer Participation", value: 74 },
            { label: "Request Success", value: 82 },
          ].map((item, i) => (
            <div key={i} className="mb-4">
              <div className="flex justify-between text-sm mb-1">
                <span>{item.label}</span>
                <span>{item.value}%</span>
              </div>
              <div className="h-3 rounded-full" style={{ backgroundColor: "#E5E7EB" }}>
                <div className="h-3 rounded-full transition-all duration-1000" style={{ width: `${item.value}%`, backgroundColor: "#22C55E" }} />
              </div>
            </div>
          ))}
        </div>

        <div className="rounded-2xl p-5 shadow-sm" style={{ backgroundColor: "#FFFFFF", border: "1px solid #D9DED3" }}>
          <h2 className="text-lg font-bold mb-4">Network Status</h2>

          <div className="grid grid-cols-2 gap-4">
            <div className="rounded-xl p-4" style={{ backgroundColor: "#E8F5E9" }}>
              <Activity className="h-5 w-5 mb-2" style={{ color: "#22C55E" }} />
              <p className="text-sm">Online Dealers</p>
              <h3 className="text-2xl font-bold"><CountUp value={data.onlineDealers} /></h3>
            </div>

            <div className="rounded-xl p-4" style={{ backgroundColor: "#FFF7ED" }}>
              <ClipboardList className="h-5 w-5 mb-2" style={{ color: "#EA580C" }} />
              <p className="text-sm">Pending Requests</p>
              <h3 className="text-2xl font-bold"><CountUp value={data.pendingRequests} /></h3>
            </div>
          </div>
        </div>
      </div>

      <div className="rounded-2xl p-5 shadow-sm mb-5" style={{ backgroundColor: "#FFFFFF", border: "1px solid #D9DED3" }}>
        <h2 className="text-lg font-bold mb-4">Quick Actions</h2>

        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <Button onClick={() => navigate("/upload")} style={{ backgroundColor: "#059669" }}>
            <Upload className="h-4 w-4 mr-2" /> Upload
          </Button>

          <Button onClick={() => navigate("/products")} style={{ backgroundColor: "#059669" }}>
            <Search className="h-4 w-4 mr-2" /> Search
          </Button>

          <Button onClick={() => navigate("/requests")} style={{ backgroundColor: "#059669" }}>
            <ClipboardList className="h-4 w-4 mr-2" /> Request
          </Button>

          <Button onClick={() => navigate("/reports")} style={{ backgroundColor: "#059669" }}>
            <BarChart3 className="h-4 w-4 mr-2" /> Reports
          </Button>
        </div>
      </div>

      <div className="rounded-2xl p-5 text-center" style={{ backgroundColor: "#059669", color: "#FFFFFF" }}>
        <h3 className="text-lg font-bold">Dealer Network Control Room</h3>
        <p className="text-sm mt-1 opacity-90">
          Master Admin can filter dealer, brand and branch. Other users see only their assigned access data.
        </p>
      </div>
    </div>
  );
}