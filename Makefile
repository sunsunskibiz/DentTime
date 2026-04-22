.PHONY: dvc-commit

dvc-commit:
	dvc add features/features_train.parquet \
	        features/features_test.parquet \
	        features/feature_stats.json \
	        src/features/artifacts/doctor_profile.json \
	        src/features/artifacts/clinic_profile.json \
	        src/features/artifacts/treatment_encoding.json
	git add features/*.dvc \
	        src/features/artifacts/doctor_profile.json.dvc \
	        src/features/artifacts/clinic_profile.json.dvc \
	        src/features/artifacts/treatment_encoding.json.dvc
	@echo ""
	@echo "DVC files staged. Now run:"
	@echo "  git commit -m 'feat: update features $(shell date +%Y-%m-%d)'"
