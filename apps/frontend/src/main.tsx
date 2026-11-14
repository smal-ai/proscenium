import { createRoot } from "react-dom/client";
import { StudioApp } from "./app/StudioApp";
import { capturePairing } from "./app/Pairing";

capturePairing();
createRoot(document.getElementById("root")!).render(<StudioApp />);
