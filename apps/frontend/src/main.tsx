import { createRoot } from "react-dom/client";
import { StudioApp } from "./app/StudioApp";
import { capturePairing } from "./app/Pairing";
import { StoryboardBrowser } from "./features/storyboards";
import { ReviewWorkspace, Outliner, CriticPanel } from "./features/review";
import { CatalogueBrowser } from "./features/catalogue";

capturePairing();
createRoot(document.getElementById("root")!).render(<StudioApp components={{
  review: ReviewWorkspace, outliner: Outliner, catalogue: CatalogueBrowser,
  storyboards: StoryboardBrowser, critic: CriticPanel,
}} />);
