FROM eclipse-temurin:23-jdk-alpine

COPY target/config-server.jar /app/config-server.jar

CMD ["java", "-jar", "/app/config-server.jar", "--spring.profiles.active=prod"]
