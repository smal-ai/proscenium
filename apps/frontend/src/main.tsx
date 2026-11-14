import { createRoot } from "react-dom/client";
import { StudioApp } from "./app/StudioApp";
import { capturePairing } from "./app/Pairing";
import { StoryboardBrowser } from "./features/storyboards";

capturePairing();
createRoot(document.getElementById("root")!).render(<StudioApp components={{ storyboards: StoryboardBrowser }} />);
