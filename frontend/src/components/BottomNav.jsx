import { Home, Map, Plus, Bell, User } from "lucide-react";
import { Link, useLocation } from "react-router";
import { useTranslation } from "react-i18next";

const BottomNav = () => {
  const { t } = useTranslation();
  const { pathname } = useLocation();

  const navItems = [
    { to: "/", icon: <Home size={24} />, label: t("home") || "Home" },
    { to: "/map", icon: <Map size={24} />, label: t("map") || "Map" },
    { to: "/new", icon: <Plus size={28} />, label: t("report") || "Report", isCenter: true },
    { to: "/alerts", icon: <Bell size={24} />, label: t("alerts") || "Alerts" },
    { to: "/profile", icon: <User size={24} />, label: t("profile") || "Profile" },
  ];

  return (
    <nav className="lg:hidden fixed bottom-0 left-0 right-0 bg-white dark:bg-black border-t border-gray-200 dark:border-[rgb(47,51,54)] z-50 safe-area-pb">
      <div className="flex justify-between items-center h-[60px] px-2">
        {navItems.map((item) => {
          const isActive = pathname === item.to;
          
          if (item.isCenter) {
            return (
              <Link 
                key={item.to} 
                to={item.to}
                className="flex flex-col items-center justify-center w-14 h-14 bg-red-500 hover:bg-red-600 rounded-full text-white shadow-lg -mt-5 transition-transform active:scale-95"
              >
                {item.icon}
              </Link>
            );
          }
          
          return (
            <Link 
              key={item.to} 
              to={item.to}
              className={`flex flex-col items-center justify-center flex-1 py-1 transition-colors ${
                isActive 
                  ? "text-sky-500" 
                  : "text-gray-400 hover:text-gray-600 dark:hover:text-gray-300"
              }`}
            >
              <div className="mb-1">{item.icon}</div>
              <span className="text-[10px] font-medium leading-none">{item.label}</span>
            </Link>
          );
        })}
      </div>
    </nav>
  );
};

export default BottomNav;
