package dk.gundmann.configserver;

import org.junit.jupiter.api.Test;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.test.web.servlet.MockMvc;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

@SpringBootTest(properties = {
        "spring.cloud.config.server.git.uri=https://example.invalid/config",
        "spring.cloud.config.server.git.username=test",
        "spring.cloud.config.server.git.password=test",
        "spring.cloud.config.server.health.enabled=false"
})
@AutoConfigureMockMvc
public class ConfigServerApplicationTests {

    @Autowired
    private MockMvc mockMvc;

	@Test
	public void contextLoads() {
	}

    @Test
    void healthIsAnActuatorResponseInsteadOfAnApplicationConfiguration() throws Exception {
        mockMvc.perform(get("/actuator/health"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("UP"));
    }

}
