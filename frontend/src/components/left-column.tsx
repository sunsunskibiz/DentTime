import { type FunctionComponent, type SyntheticEvent, useState, useCallback, useEffect, useMemo } from "react";

export type LeftColumnType = {
  className?: string;
  onPredict?: (data: PredictFormData) => void;
  isLoading?: boolean;
};

export interface PredictFormData {
  treatmentSymptoms: string;
  toothNumbers: string;
  surfaces: string;
  timeOfDay: string;
  dayOfWeek: string;
  appointmentRankInDay?: number;
  doctorId?: string;
  clinicId: string;
  isFirstCase: boolean;
  notes?: string;
}

interface Doctor {
  id: string;
  doctor: string;
}
interface Clinic {
  id: string;
  clinic: string;
}
interface PredictOptions {
  doctors: Doctor[];
  clinics: Clinic[];
}

const MAX_TOOTH_TAGS = 32;

/**
 * Patient & Treatment Details form panel.
 * Collects treatment inputs and emits a prediction request.
 */
const LeftColumn: FunctionComponent<LeftColumnType> = ({
  className = "",
  onPredict,
  isLoading = false,
}) => {
  const [predictOption, setPredictOption] = useState<PredictOptions | null>(null);
  const [toothNumbers, setToothNumbers] = useState<string>("");
  const [toothInput, setToothInput] = useState("");
  const [toothInputError, setToothInputError] = useState("");
  const [toothType, setToothType] = useState<'normal' | 'full-mouth' | 'upper' | 'lower'>('normal');
  const [surfaces, setSurfaces] = useState<string>("");
  const [surfaceInput, setSurfaceInput] = useState("");
  const [surfaceInputError, setSurfaceInputError] = useState("");
  const [isFirstCase, setIsFirstCase] = useState(false);
  const [treatment, setTreatment] = useState("");
  const [timeOfDay, setTimeOfDay] = useState("0");
  const [dayOfWeek, setDayOfWeek] = useState("0");
  const [appointmentRankInDay, setAppointmentRankInDay] = useState<number | undefined>();
  const [appointmentRankError, setAppointmentRankError] = useState("");
  const [doctorId, setDoctorId] = useState("");
  const [doctorSearch, setDoctorSearch] = useState("");
  const [doctorInputError, setDoctorInputError] = useState("");
  const [isDoctorMenuOpen, setIsDoctorMenuOpen] = useState(false);
  const [isNewDoctor, setIsNewDoctor] = useState(false);
  const [clinicId, setClinicId] = useState("");
  const [clinicSearch, setClinicSearch] = useState("");
  const [isClinicMenuOpen, setIsClinicMenuOpen] = useState(false);
  const [notes, setNotes] = useState("");
  // const hasInvalidCount = toothNumbers.length === 0;

  useEffect(() => {
    fetch(`${import.meta.env.VITE_API_URL}/options`)
      .then((res) => res.json())
      .then((data) => {
        setPredictOption({
          doctors: data.doctors,
          clinics: data.clinics,
        });
      });

  }, []);

  const handleToggle = useCallback(() => {
    setIsFirstCase((prev) => !prev);
  }, []);

  const handleSubmit = useCallback(
    (e: SyntheticEvent<HTMLFormElement>) => {
      e.preventDefault();
      const trimmedDoctorSearch = doctorSearch.trim();
      const effectiveDoctorId = isNewDoctor ? trimmedDoctorSearch : doctorId;

      if (!isNewDoctor && trimmedDoctorSearch && !doctorId) {
        setDoctorInputError("Please select a valid doctor or check New doctor.");
        return;
      }

      if (appointmentRankError) {
        return;
      }

      setDoctorInputError("");
      onPredict?.({
        treatmentSymptoms: treatment,
        toothNumbers: toothNumbers.trim() || "none",
        surfaces: surfaces.trim() || "none",
        timeOfDay,
        dayOfWeek,
        appointmentRankInDay,
        doctorId: effectiveDoctorId || undefined,
        clinicId,
        isFirstCase,
        notes,
      });
    },
    [treatment, toothNumbers, surfaces, timeOfDay, dayOfWeek, appointmentRankInDay, dayOfWeek, appointmentRankInDay, doctorId, clinicId, isNewDoctor, doctorSearch, isFirstCase, notes, onPredict]
  );

  const addToothNumber = useCallback((rawValue: string) => {
    const cleaned = rawValue.trim();
    if (!cleaned) return;

    const num = Number(cleaned);
    const isValid = !Number.isNaN(num) && num >= 1;
    if (!isValid) {
      setToothInputError("Tooth number must be a positive number.");
      return;
    }

    setToothNumbers((prev) => {
      const current = prev ? prev.split(',').map(s => s.trim()) : [];
      if (current.length >= MAX_TOOTH_TAGS) {
        setToothInputError(`You can add up to ${MAX_TOOTH_TAGS} tooth numbers.`);
        return prev;
      }
      if (current.includes(cleaned)) return prev;
      setToothInputError("");
      const newList = [...current, cleaned];
      return newList.join(',');
    });
  }, []);

  const removeToothNumber = useCallback((value: string) => {
    setToothNumbers((prev) => {
      const current = prev ? prev.split(',').map(s => s.trim()) : [];
      const filtered = current.filter((item) => item !== value);
      return filtered.join(',');
    });
  }, []);

  const addSurface = useCallback((rawValue: string) => {
    const cleaned = rawValue.trim();
    if (!cleaned) return;

    const num = Number(cleaned);
    const isValid = !Number.isNaN(num) && num >= 1 && num <= 5;
    if (!isValid) {
      setSurfaceInputError("Surface number must be between 1 and 5.");
      return;
    }

    setSurfaces((prev) => {
      const current = prev ? prev.split(',').map(s => s.trim()) : [];
      if (current.length >= 5) {
        setSurfaceInputError("You can add up to 5 surface numbers.");
        return prev;
      }
      if (current.includes(cleaned)) return prev;
      setSurfaceInputError("");
      const newList = [...current, cleaned];
      return newList.join(',');
    });
  }, []);

  const removeSurface = useCallback((value: string) => {
    setSurfaces((prev) => {
      const current = prev ? prev.split(',').map(s => s.trim()) : [];
      const filtered = current.filter((item) => item !== value);
      return filtered.join(',');
    });
  }, []);

  const filteredDoctors = useMemo(() => {
    const allDoctors = predictOption?.doctors || [];
    const query = doctorSearch.trim().toLowerCase();
    if (!query) return allDoctors;
    return allDoctors.filter((item) => item.doctor.toLowerCase().includes(query));
  }, [predictOption?.doctors, doctorSearch]);

  const selectDoctor = useCallback((doctor: Doctor) => {
    setDoctorId(doctor.id);
    setDoctorSearch(doctor.doctor);
    setIsDoctorMenuOpen(false);
    setIsNewDoctor(false);
  }, []);

  const filteredClinics = useMemo(() => {
    const allClinics = predictOption?.clinics || [];
    const query = clinicSearch.trim().toLowerCase();
    if (!query) return allClinics;
    return allClinics.filter((item) => item.clinic.toLowerCase().includes(query));
  }, [predictOption?.clinics, clinicSearch]);

  const selectClinic = useCallback((clinic: Clinic) => {
    setClinicId(clinic.id);
    setClinicSearch(clinic.clinic);
    setIsClinicMenuOpen(false);
  }, []);

  return (
    <form
      aria-label="Patient and treatment details"
      onSubmit={handleSubmit}
      className={`!m-0 flex-1 shadow-[0px_4px_20px_rgba(14,_37,_56,_0.05)] rounded-2xl bg-[#fff] border-[#e0edfa] border-solid border-[1px] overflow-hidden flex flex-col items-start !pt-[30px] !pb-[30px] !pl-[31px] !pr-[31px] gap-5 mq725:!pt-5 mq725:!pb-5 mq725:box-border ${className}`}
    >
      {/* Header row */}
      <div className="self-stretch overflow-hidden flex items-center justify-between gap-5 mq725:flex-wrap mq725:gap-5">
        <b className="relative text-lg font-[Inter] text-[#0e2538] text-left">
          Patient &amp; Treatment Details
        </b>
        <div className="rounded-[20px] bg-[#def7fc] overflow-hidden flex items-center justify-center !pt-[5px] !pb-[5px] !pl-3 !pr-3">
          <span className="relative text-xs font-medium font-[Inter] text-[#0e7da1] text-left">
            Step 1 of 1
          </span>
        </div>
      </div>

      {/* Divider */}
      <div
        role="separator"
        className="self-stretch h-px bg-[#e0edfa] overflow-hidden shrink-0 flex flex-col items-start"
      />

      {/* Treatment / Symptoms textarea */}
      <div className="self-stretch overflow-hidden flex flex-col items-start gap-1.5">
        <label
          htmlFor="treatment"
          className="relative text-[13px] font-medium font-[Inter] text-[#0e2538] text-left"
        >
          Treatment / Symptoms
        </label>
        <textarea
          id="treatment"
          value={treatment}
          onChange={(e) => setTreatment(e.target.value)}
          placeholder="Describe the treatment or symptoms (optional)..."
          aria-label="Treatment description"
          className="self-stretch rounded-lg bg-[#fff] border-[#e0edfa] border-solid border-[1px] overflow-hidden !p-3 text-sm font-[Inter] text-[#708599] text-left outline-none focus:border-[#0e7da1] transition-colors resize-none min-h-[100px]"
        />
      </div>


      {/* Notes textarea */}
      <div className="self-stretch overflow-hidden flex flex-col items-start gap-1.5">
        <label
          htmlFor="notes"
          className="relative text-[13px] font-medium font-[Inter] text-[#0e2538] text-left"
        >
          Additional Notes
        </label>
        <textarea
          id="notes"
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          placeholder="Add any additional notes or observations..."
          aria-label="Additional notes"
          className="self-stretch rounded-lg bg-[#fff] border-[#e0edfa] border-solid border-[1px] overflow-hidden !p-3 text-sm font-[Inter] text-[#708599] text-left outline-none focus:border-[#0e7da1] transition-colors resize-none min-h-[100px]"
        />
      </div>

      {/* Tooth Numbers + Time of Day + Day of Week row */}
      <div className="self-stretch overflow-hidden flex items-start gap-4 mq1000:flex-wrap">
        <div className="flex-1 overflow-hidden flex flex-col items-start gap-1.5 min-w-[337px] mq725:min-w-full">
          <label
            htmlFor="tooth-type"
            className="relative text-[13px] font-medium font-[Inter] text-[#0e2538] text-left"
          >
            Tooth Selection
          </label>
          <select
            id="tooth-type"
            value={toothType}
            onChange={(e) => {
              const newType = e.target.value as 'normal' | 'full-mouth' | 'upper' | 'lower';
              setToothType(newType);
              if (newType === 'normal') {
                setToothNumbers("");
                setSurfaces("");
              } else {
                const typeMap = {
                  'full-mouth': 'Full mouth',
                  'upper': 'Upper',
                  'lower': 'Lower'
                };
                setToothNumbers(typeMap[newType]);
                setSurfaces("none");
              }
              setToothInputError("");
              setSurfaceInputError("");
            }}
            aria-label="Select tooth type"
            className="self-stretch rounded-lg bg-[#fff] border-[#e0edfa] border-solid border-[1px] overflow-hidden flex items-center justify-between !pt-[9px] !pb-[9px] !pl-3.5 !pr-3.5 text-sm font-[Inter] text-[#708599] text-left outline-none focus:border-[#0e7da1] transition-colors cursor-pointer mb-2"
          >
            <option value="normal">Specific (FDI) tooth numbers</option>
            <option value="full-mouth">Full mouth</option>
            <option value="upper">Upper</option>
            <option value="lower">Lower</option>
          </select>
          {toothType === 'normal' && (
            <>
              <div className="self-stretch rounded-lg bg-[#fff] border-[#e0edfa] border-solid border-[1px] overflow-hidden !p-2 flex flex-wrap items-center gap-2 focus-within:border-[#0e7da1] transition-colors">
                {(toothNumbers ? toothNumbers.split(',').map(s => s.trim()).filter(s => s) : []).map((tooth) => (
                  <span
                    key={tooth}
                    className="inline-flex items-center gap-1 rounded-full bg-[#def7fc] text-[#0e7da1] text-xs font-medium font-[Inter] !pl-2.5 !pr-2 !pt-1 !pb-1"
                  >
                    {tooth}
                    <button
                      type="button"
                      aria-label={`Remove tooth number ${tooth}`}
                      onClick={() => removeToothNumber(tooth)}
                      className="border-0 bg-transparent text-[#0e7da1] cursor-pointer leading-none !p-0"
                    >
                      x
                    </button>
                  </span>
                ))}
                <input
                  id="tooth-numbers"
                  value={toothInput}
                  onChange={(e) => {
                    setToothInput(e.target.value);
                    if (toothInputError) setToothInputError("");
                  }}
                  onBlur={() => {
                    addToothNumber(toothInput);
                    setToothInput("");
                  }}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === "," || e.key === "Tab") {
                      e.preventDefault();
                      addToothNumber(toothInput);
                      setToothInput("");
                    }
                    if (
                      e.key === "Backspace" &&
                      toothInput.trim() === "" &&
                      toothNumbers
                    ) {
                      e.preventDefault();
                      const current = toothNumbers.split(',').map(s => s.trim()).filter(s => s);
                      if (current.length > 0) {
                        const newList = current.slice(0, -1);
                        setToothNumbers(newList.join(','));
                      }
                    }
                  }}
                  placeholder={(toothNumbers && toothNumbers.split(',').filter(s => s.trim()).length) ? "Add more..." : "Type tooth number and press Enter"}
                  aria-label="Enter tooth numbers"
                  className="flex-1 min-w-[160px] border-0 outline-none text-sm font-[Inter] text-[#0e2538] !p-1 placeholder:text-[#708599]"
                />
              </div>
              {toothInputError && (
                <span className="text-xs font-[Inter] text-[#b91c1c]">{toothInputError}</span>
              )}
            </>
          )}
          {toothType === 'normal' && (
            <>
              <label
                htmlFor="surfaces"
                className="relative text-[13px] font-medium font-[Inter] text-[#0e2538] text-left mt-4"
              >
                Surface Numbers
              </label>
              <div className="self-stretch rounded-lg bg-[#fff] border-[#e0edfa] border-solid border-[1px] overflow-hidden !p-2 flex flex-wrap items-center gap-2 focus-within:border-[#0e7da1] transition-colors">
                {(surfaces ? surfaces.split(',').map(s => s.trim()).filter(s => s) : []).map((surface) => (
                  <span
                    key={surface}
                    className="inline-flex items-center gap-1 rounded-full bg-[#def7fc] text-[#0e7da1] text-xs font-medium font-[Inter] !pl-2.5 !pr-2 !pt-1 !pb-1"
                  >
                    {surface}
                    <button
                      type="button"
                      aria-label={`Remove surface number ${surface}`}
                      onClick={() => removeSurface(surface)}
                      className="border-0 bg-transparent text-[#0e7da1] cursor-pointer leading-none !p-0"
                    >
                      x
                    </button>
                  </span>
                ))}
                <input
                  id="surfaces"
                  value={surfaceInput}
                  onChange={(e) => {
                    setSurfaceInput(e.target.value);
                    if (surfaceInputError) setSurfaceInputError("");
                  }}
                  onBlur={() => {
                    addSurface(surfaceInput);
                    setSurfaceInput("");
                  }}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === "," || e.key === "Tab") {
                      e.preventDefault();
                      addSurface(surfaceInput);
                      setSurfaceInput("");
                    }
                    if (
                      e.key === "Backspace" &&
                      surfaceInput.trim() === "" &&
                      surfaces
                    ) {
                      e.preventDefault();
                      const current = surfaces.split(',').map(s => s.trim()).filter(s => s);
                      if (current.length > 0) {
                        const newList = current.slice(0, -1);
                        setSurfaces(newList.join(','));
                      }
                    }
                  }}
                  placeholder={(surfaces && surfaces.split(',').filter(s => s.trim()).length) ? "Add more..." : "Type surface number and press Enter"}
                  aria-label="Enter surface numbers"
                  className="flex-1 min-w-[160px] border-0 outline-none text-sm font-[Inter] text-[#0e2538] !p-1 placeholder:text-[#708599]"
                />
              </div>
              {surfaceInputError && (
                <span className="text-xs font-[Inter] text-[#b91c1c]">{surfaceInputError}</span>
              )}
            </>
          )}
          {toothType !== 'normal' && (
            <div className="self-stretch rounded-lg bg-[#fff] border-[#e0edfa] border-solid border-[1px] overflow-hidden !p-3 text-sm font-[Inter] text-[#0e2538]">
              {toothNumbers}
            </div>
          )}
        </div>
        <div className="flex-1 overflow-hidden flex flex-col items-start gap-1.5 min-w-[200px] mq725:min-w-full">
          <label
            htmlFor="time-of-day"
            className="relative text-[13px] font-medium font-[Inter] text-[#0e2538] text-left"
          >
            Time of Day
          </label>
          <select
            id="time-of-day"
            value={timeOfDay}
            onChange={(e) => setTimeOfDay(e.target.value)}
            aria-label="Select time of day"
            className="self-stretch rounded-lg bg-[#fff] border-[#e0edfa] border-solid border-[1px] overflow-hidden flex items-center justify-between !pt-[9px] !pb-[9px] !pl-3.5 !pr-3.5 text-sm font-[Inter] text-[#708599] text-left outline-none focus:border-[#0e7da1] transition-colors cursor-pointer"
          >
            <option value="0">Unknown / ไม่มีข้อมูล</option>
            <option value="4">04:00–07:59</option>
            <option value="8">08:00–11:59</option>
            <option value="12">12:00–15:59</option>
            <option value="16">16:00–19:59</option>
            <option value="20">20:00–23:59</option>
          </select>
        </div>
        <div className="flex-1 overflow-hidden flex flex-col items-start gap-1.5 min-w-[200px] mq725:min-w-full">
          <label
            htmlFor="day-of-week"
            className="relative text-[13px] font-medium font-[Inter] text-[#0e2538] text-left"
          >
            Day of Week
          </label>
          <select
            id="day-of-week"
            value={dayOfWeek}
            onChange={(e) => setDayOfWeek(e.target.value)}
            aria-label="Select day of week"
            className="self-stretch rounded-lg bg-[#fff] border-[#e0edfa] border-solid border-[1px] overflow-hidden flex items-center justify-between !pt-[9px] !pb-[9px] !pl-3.5 !pr-3.5 text-sm font-[Inter] text-[#708599] text-left outline-none focus:border-[#0e7da1] transition-colors cursor-pointer"
          >
            <option value="0">Monday</option>
            <option value="1">Tuesday</option>
            <option value="2">Wednesday</option>
            <option value="3">Thursday</option>
            <option value="4">Friday</option>
            <option value="5">Saturday</option>
            <option value="6">Sunday</option>
          </select>
        </div>
        <div className="flex-1 overflow-hidden flex flex-col items-start gap-1.5 min-w-[337px] mq725:min-w-full">
          <label
            htmlFor="appointment-rank-in-day"
            className="relative text-[13px] font-medium font-[Inter] text-[#0e2538] text-left"
          >
            Appointment Rank in Day
          </label>
          <input
            id="appointment-rank-in-day"
            type="number"
            value={appointmentRankInDay ?? ""}
            onChange={(e) => {
              const val = e.target.value;
              const num = val === "" ? undefined : Number(val);
              setAppointmentRankInDay(num);
              if (num !== undefined && (!Number.isInteger(num) || num < 1)) {
                setAppointmentRankError("Appointment rank must be a positive integer (1 or greater).");
              } else {
                setAppointmentRankError("");
              }
            }}
            placeholder="e.g. 1, 2, 3..."
            aria-label="Appointment rank in day"
            className="self-stretch rounded-lg bg-[#fff] border-[#e0edfa] border-solid border-[1px] overflow-hidden !pt-[9px] !pb-[9px] !pl-3.5 !pr-3.5 text-sm font-[Inter] text-[#708599] text-left outline-none focus:border-[#0e7da1] transition-colors"
          />
          {appointmentRankError && (
            <span className="text-xs font-[Inter] text-[#b91c1c]">{appointmentRankError}</span>
          )}
        </div>
      </div>

      {/* Doctor search dropdown */}
      <div className="self-stretch overflow-hidden flex flex-col items-start gap-1.5 relative">
        <label
          htmlFor="doctor-search"
          className="relative text-[13px] font-medium font-[Inter] text-[#0e2538] text-left"
        >
          Doctor (Anonymized ID)
        </label>
        <div className="self-stretch rounded-lg bg-[#fff] border-[#e0edfa] border-solid border-[1px] overflow-hidden !p-2 flex flex-col gap-0 transition-colors focus-within:border-[#0e7da1]">
          <input
            id="doctor-search"
            value={doctorSearch}
            onChange={(e) => {
              setDoctorSearch(e.target.value);
              setDoctorId("");
              setDoctorInputError("");
              setIsDoctorMenuOpen(!isNewDoctor);
            }}
            onFocus={() => setIsDoctorMenuOpen(!isNewDoctor)}
            onBlur={() => {
              window.setTimeout(() => setIsDoctorMenuOpen(false), 150);
            }}
            placeholder={isNewDoctor ? "Type new doctor name..." : "Search doctor by name..."}
            aria-label="Search doctor"
            className="self-stretch border-0 outline-none text-sm font-[Inter] text-[#0e2538] !p-1 placeholder:text-[#708599]"
            autoComplete="off"
          />
          <div className="mt-2 flex flex-col gap-2 text-sm">
            <div className="flex items-center gap-2">
              <label className="inline-flex items-center gap-2 text-[#0e2538]">
                <input
                  type="checkbox"
                  checked={isNewDoctor}
                  onChange={(e) => {
                    const checked = e.target.checked;
                    setIsNewDoctor(checked);
                    setDoctorInputError("");
                    if (checked) {
                      setDoctorId("");
                      setIsDoctorMenuOpen(false);
                    } else if (doctorSearch.trim()) {
                      setIsDoctorMenuOpen(true);
                    }
                  }}
                  className="h-4 w-4 rounded border-[#d1d5db] text-[#0e7da1] focus:ring-[#0e7da1]"
                />
                New doctor
              </label>
              <span className="text-[#708599]">(leave blank if unknown)</span>
            </div>
            {doctorInputError && (
              <span className="text-xs text-[#b91c1c]">{doctorInputError}</span>
            )}
          </div>
          {isDoctorMenuOpen && !isNewDoctor && filteredDoctors.length > 0 && (
            <div className="mt-1 w-full rounded-lg bg-[#fff] border-[#e0edfa] border-solid border-[1px] shadow-md max-h-52 overflow-auto z-20">
              {filteredDoctors.map((doctor) => (
                <button
                  key={doctor.id}
                  type="button"
                  onMouseDown={(e) => e.preventDefault()}
                  onClick={() => selectDoctor(doctor)}
                  className="w-full text-left border-0 bg-transparent !px-3 !py-2 text-sm font-[Inter] text-[#0e2538] hover:bg-[#f5faff] cursor-pointer"
                >
                  {doctor.doctor}
                </button>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Clinic search dropdown */}
      <div className="self-stretch overflow-hidden flex flex-col items-start gap-1.5 relative">
        <label
          htmlFor="clinic-search"
          className="relative text-[13px] font-medium font-[Inter] text-[#0e2538] text-left"
        >
          Clinic (Anonymized ID)
        </label>
        <div className="self-stretch rounded-lg bg-[#fff] border-[#e0edfa] border-solid border-[1px] overflow-hidden !p-2 flex flex-col gap-0 transition-colors focus-within:border-[#0e7da1]">
          <input
            id="clinic-search"
            value={clinicSearch}
            onChange={(e) => {
              setClinicSearch(e.target.value);
              setClinicId("");
              setIsClinicMenuOpen(true);
            }}
            onFocus={() => setIsClinicMenuOpen(true)}
            onBlur={() => {
              window.setTimeout(() => setIsClinicMenuOpen(false), 150);
            }}
            placeholder="Search clinic by name..."
            aria-label="Search clinic"
            className="self-stretch border-0 outline-none text-sm font-[Inter] text-[#0e2538] !p-1 placeholder:text-[#708599]"
            autoComplete="off"
          />
          {isClinicMenuOpen && filteredClinics.length > 0 && (
            <div className="mt-1 w-full rounded-lg bg-[#fff] border-[#e0edfa] border-solid border-[1px] shadow-md max-h-52 overflow-auto z-20">
              {filteredClinics.map((clinic) => (
                <button
                  key={clinic.id}
                  type="button"
                  onMouseDown={(e) => e.preventDefault()}
                  onClick={() => selectClinic(clinic)}
                  className="w-full text-left border-0 bg-transparent !px-3 !py-2 text-sm font-[Inter] text-[#0e2538] hover:bg-[#f5faff] cursor-pointer"
                >
                  {clinic.clinic}
                </button>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* First Case toggle */}
      <div className="self-stretch rounded-[10px] bg-[#f0faff] border-[#e0edfa] border-solid border-[1px] overflow-hidden flex items-center justify-between !pt-3 !pb-3 !pl-4 !pr-4 gap-5">
        <div className="overflow-hidden flex flex-col items-start gap-[3px]">
          <span className="relative text-sm font-medium font-[Inter] text-[#0e2538] text-left">
            Is First Case of the Day?
          </span>
          <span className="relative text-xs font-[Inter] text-[#708599] text-left">
            First case patterns affect duration prediction accuracy.
          </span>
        </div>
        <button
          type="button"
          role="switch"
          aria-checked={isFirstCase}
          aria-label="Toggle first case of the day"
          onClick={handleToggle}
          className={`relative rounded-xl overflow-hidden flex items-center justify-end !p-0.5 cursor-pointer border-0 transition-all duration-300 ease-in-out transform hover:scale-105 ${
            isFirstCase
              ? "bg-gradient-to-r from-[#0e7da1] to-[#0b6a8a] shadow-lg shadow-[#0e7da1]/30 ring-2 ring-[#0e7da1]/20"
              : "bg-[#d1d5db] hover:bg-[#c1c5c9]"
          }`}
        >
          <span
            className={`h-5 w-5 rounded-[10px] bg-[#fff] overflow-hidden shrink-0 flex flex-col items-start transition-all duration-300 ease-in-out shadow-md ${
              isFirstCase ? "translate-x-0" : "-translate-x-5"
            }`}
          />
          {isFirstCase && (
            <div className="absolute inset-0 rounded-xl bg-gradient-to-r from-[#0e7da1]/10 to-[#0b6a8a]/10 animate-pulse" />
          )}
        </button>
      </div>

      {/* Submit button */}
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
          {isLoading ? "Predicting..." : "Predict Treatment Duration →"}
        </span>
      </button>
    </form>
  );
};

export default LeftColumn;