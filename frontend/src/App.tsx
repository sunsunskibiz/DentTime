import { useEffect } from "react";
import {
  Routes,
  Route,
  useNavigationType,
  useLocation,
} from "react-router-dom";
import Landing from "./pages/landing";
import Login from "./pages/login";
import Predict from "./pages/predict";
import HowItWorks from "./pages/how-it-works";
import About from "./pages/about";

function App() {
  const action = useNavigationType();
  const location = useLocation();
  const pathname = location.pathname;

  useEffect(() => {
    if (action !== "POP") {
      window.scrollTo(0, 0);
    }
  }, [action, pathname]);

  useEffect(() => {
    let title = "";
    let metaDescription = "";

    switch (pathname) {
      case "/":
        title = "Login — DentTime";
        metaDescription = "Login to DentTime to access the appointment duration predictor.";
        break;
      case "/home":
        title = "DentTime — AI-Powered Smart Dentist Scheduling";
        metaDescription = "DentTime predicts the right treatment duration so clinic staff can book smarter appointments.";
        break;
      case "/predict":
        title = "Predict Appointment Duration — DentTime";
        metaDescription = "Fill in patient details to get an AI-predicted treatment time window.";
        break;
      case "/how-it-works":
        title = "How It Works — DentTime";
        metaDescription = "Learn how DentTime's ML pipeline turns raw clinic data into real-time appointment scheduling support.";
        break;
      case "/about":
        title = "About — DentTime";
        metaDescription = "Learn more about DentTime's mission to revolutionize dental appointment scheduling with AI.";
        break;
    }

    if (title) {
      document.title = title;
    }

    if (metaDescription) {
      const metaDescriptionTag: HTMLMetaElement | null = document.querySelector(
        'head > meta[name="description"]'
      );
      if (metaDescriptionTag) {
        metaDescriptionTag.content = metaDescription;
      }
    }
  }, [pathname]);

  return (
    <Routes>
      <Route path="/" element={<Login />} />
      <Route path="/home" element={<Landing />} />
      <Route path="/predict" element={<Predict />} />
      <Route path="/how-it-works" element={<HowItWorks />} />
      <Route path="/about" element={<About />} />
    </Routes>
  );
}

export default App;
