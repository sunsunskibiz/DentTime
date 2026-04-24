import { type FunctionComponent, useState, useCallback, type SyntheticEvent } from "react";
import { useNavigate } from "react-router-dom";

const Login: FunctionComponent = () => {
  const navigate = useNavigate();
  const [userId, setUserId] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [isLoading, setIsLoading] = useState(false);

  const handleSubmit = useCallback(
    (e: SyntheticEvent<HTMLFormElement>) => {
      e.preventDefault();
      setError("");
      setIsLoading(true);

      // Simple hardcoded validation
      if (userId.trim() === "dentime" && password === "admin") {
        // Store login state in localStorage
        localStorage.setItem("isAuthenticated", "true");
        localStorage.setItem("userId", userId);
        navigate("/predict");
      } else {
        setError("Invalid ID or password.");
        setUserId("");
        setPassword("");
      }

      setIsLoading(false);
    },
    [userId, password, navigate]
  );

  return (
    <div className="w-full min-h-screen bg-[#f0faff] flex items-center justify-center !pt-8 !pb-8 !pl-4 !pr-4">
      <div className="w-full max-w-[400px] shadow-[0px_4px_20px_rgba(14,_37,_56,_0.05)] rounded-2xl bg-[#fff] border-[#e0edfa] border-solid border-[1px] overflow-hidden flex flex-col items-start !pt-[40px] !pb-[40px] !pl-[30px] !pr-[30px] gap-8">
        {/* Header */}
        <div className="self-stretch overflow-hidden flex flex-col items-center gap-3">
          <h1 className="!m-0 relative text-2xl font-bold font-[Inter] text-[#0e2538] text-center">
            DentTime
          </h1>
          <p className="!m-0 relative text-sm font-[Inter] text-[#708599] text-center">
            Treatment Duration Predictor
          </p>
        </div>

        {/* Divider */}
        <div
          role="separator"
          className="self-stretch h-px bg-[#e0edfa] overflow-hidden shrink-0"
        />

        {/* Form */}
        <form onSubmit={handleSubmit} className="self-stretch overflow-hidden flex flex-col items-start gap-4">
          {/* ID Field */}
          <div className="self-stretch overflow-hidden flex flex-col items-start gap-1.5">
            <label
              htmlFor="user-id"
              className="relative text-[13px] font-medium font-[Inter] text-[#0e2538] text-left"
            >
              ID
            </label>
            <input
              id="user-id"
              type="text"
              value={userId}
              onChange={(e) => {
                setUserId(e.target.value);
                if (error) setError("");
              }}
              placeholder="Enter your ID"
              aria-label="User ID"
              className="self-stretch rounded-lg bg-[#fff] border-[#e0edfa] border-solid border-[1px] overflow-hidden !pt-[9px] !pb-[9px] !pl-3.5 !pr-3.5 text-sm font-[Inter] text-[#708599] text-left outline-none focus:border-[#0e7da1] transition-colors"
            />
          </div>

          {/* Password Field */}
          <div className="self-stretch overflow-hidden flex flex-col items-start gap-1.5">
            <label
              htmlFor="password"
              className="relative text-[13px] font-medium font-[Inter] text-[#0e2538] text-left"
            >
              Password
            </label>
            <input
              id="password"
              type="password"
              value={password}
              onChange={(e) => {
                setPassword(e.target.value);
                if (error) setError("");
              }}
              placeholder="Enter your password"
              aria-label="Password"
              className="self-stretch rounded-lg bg-[#fff] border-[#e0edfa] border-solid border-[1px] overflow-hidden !pt-[9px] !pb-[9px] !pl-3.5 !pr-3.5 text-sm font-[Inter] text-[#708599] text-left outline-none focus:border-[#0e7da1] transition-colors"
            />
          </div>

          {/* Error Message */}
          {error && (
            <div className="self-stretch rounded-lg bg-[#fee] border-[#fcc] border-solid border-[1px] overflow-hidden !pt-3 !pb-3 !pl-3 !pr-3">
              <span className="relative text-sm font-[Inter] text-[#b91c1c]">
                {error}
              </span>
            </div>
          )}

          {/* Submit Button */}
          <button
            type="submit"
            disabled={isLoading}
            className={`self-stretch rounded-[10px] overflow-hidden flex items-center justify-center !pt-4 !pb-4 !pl-0 !pr-0 border-0 transition-colors ${
              isLoading
                ? "bg-[#7fb8c9] cursor-not-allowed"
                : "bg-[#0e7da1] cursor-pointer hover:bg-[#0b6a8a]"
            }`}
          >
            <span className="relative text-base font-semibold font-[Inter] text-[#fff] text-left">
              {isLoading ? "Logging in..." : "Login"}
            </span>
          </button>
        </form>

        {/* Footer Note */}
        <div className="self-stretch overflow-hidden flex flex-col items-center gap-2">
          <p className="!m-0 relative text-xs font-[Inter] text-[#708599] text-center">
            Demo credentials:
          </p>
          <p className="!m-0 relative text-xs font-[Inter] text-[#0e7da1] text-center font-medium">
            ID: dentime / Password: admin
          </p>
        </div>
      </div>
    </div>
  );
};

export default Login;
