import { type FunctionComponent } from "react";
import Navbar from "../components/navbar";

/**
 * About page — information about the DentTime project.
 */
const About: FunctionComponent = () => {
  return (
    <div className="w-full relative bg-[#f0faff] flex flex-col items-center leading-[normal] tracking-[normal]">
      <Navbar />

      {/* Hero Banner */}
      <section
        aria-labelledby="about-heading"
        className="self-stretch bg-[#0e7da1] overflow-hidden flex flex-col items-center justify-center !pt-16 !pb-16 !pl-20 !pr-20 gap-4 text-left text-xs text-[#b8e8f5] font-[Inter] mq1050:!pl-10 mq1050:!pr-10 mq1050:box-border"
      >

        <h1
          id="about-heading"
          className="!m-0 relative text-[44px] font-extrabold font-[inherit] text-[#fff] mq450:text-[26px] mq1050:text-[35px]"
        >
          About DentTime
        </h1>
        <p className="!m-0 relative text-[17px]">
          Learn more about our mission to revolutionize dental appointment scheduling with AI.
        </p>
      </section>

      {/* About Content */}
      <section className="self-stretch overflow-hidden flex flex-col items-center justify-start !pt-16 !pb-16 !pl-20 !pr-20 gap-8 text-left text-[#0e2538] font-[Inter] mq1050:!pl-10 mq1050:!pr-10 mq1050:box-border mq750:gap-8">
        <div className="w-full max-w-[800px] overflow-hidden flex flex-col items-start gap-6">
          <h2 className="m-0 relative text-2xl font-bold font-[Inter] text-[#0e2538] text-left">
            Our Mission
          </h2>
          <p className="!m-0 relative text-base font-[Inter] text-[#33455c] text-left">
            DentTime is an AI-powered tool designed to help dental clinics optimize their appointment scheduling by predicting treatment durations accurately. By leveraging machine learning on historical clinic data, we provide real-time insights to reduce patient wait times and improve operational efficiency.
          </p>

          <h2 className="m-0 relative text-2xl font-bold font-[Inter] text-[#0e2538] text-left">
            How It Works
          </h2>
          <p className="!m-0 relative text-base font-[Inter] text-[#33455c] text-left">
            Our system analyzes various factors including treatment type, tooth numbers, time of day, day of week, doctor experience, clinic location, and whether it's the first case of the day. The ML model predicts treatment duration classes to help clinics make informed scheduling decisions.
          </p>

          <h2 className="m-0 relative text-2xl font-bold font-[Inter] text-[#0e2538] text-left">
            Technology Stack
          </h2>
          <p className="!m-0 relative text-base font-[Inter] text-[#33455c] text-left">
            Built with modern web technologies including React and TypeScript for the frontend, FastAPI and Python for the backend, and MLflow for model management. The system uses scikit-learn and other ML libraries for predictive modeling.
          </p>

          <h2 className="m-0 relative text-2xl font-bold font-[Inter] text-[#0e2538] text-left">
            Contact
          </h2>
          <p className="!m-0 relative text-base font-[Inter] text-[#33455c] text-left">
            For questions or feedback about DentTime, please reach out to our development team.
          </p>
        </div>
      </section>

      <footer className="self-stretch bg-[#0e2538] overflow-hidden flex items-center justify-center !pt-7 !pb-7 !pl-20 !pr-20 box-border max-w-full text-left text-sm text-[#fff] font-[Inter] mq750:!pl-10 mq750:!pr-10 mq750:box-border">
        <span className="relative font-semibold">
        DentTime — Software Engineering for Machine Learning (SEML) Project 2025 • Team Two to one
        </span>
      </footer>
    </div>
  );
};

export default About;